<!-- task-pipeline: validated -->
# Subtask 5ee2ee50 — Port the review gate, the plan-hash gate and `count_of`

Parent story: `90d6bbf6` "Gates: the reducers ported from task.js" (milestone `352e955b`, Milestone 1: walking skeleton). Source of truth: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §5 "Reducers to port faithfully", §6 phase contract, §14 Testing. Behavioural specification: `.claude/workflows/task.js` PURE region (lines 85, 92-108, 163-194) and the sibling plugin's `/home/paulomtts/Code/leave-me-alone/plugins/leave-me-alone/workflows/task.test.mjs` lines 59-138 and 189-219.

## Scope

Add three more pure reducers to the existing `src/agent_manager/steps/reducers.py` — `count_of`, `review_gate`, and the plan-hash logic — and their unit tests to the existing `tests/steps/test_reducers.py`. Work resumes from branch `m1/task-port-the-exploration-ef33352b` (worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m1/task-port-the-exploration-ef33352b`), which already carries `verification_gate`, `exploration_output_gate` and the helpers `_json` / `_field`. Those two gates and their tests are the done sibling's (`ef33352b`) and are not to be re-touched; the helpers may be reused.

Out of scope: `short_id` / `stem_of` (already ported elsewhere by `01d725d6`), the `plan_check` and `verify.run_suite` steps that will *call* these gates (`d3feb87e`, `9c3b1ffb`), `status_write_outcome`, `shell_quote` (§5: does not port), and all milestone-level orchestration.

Invariants carried from the story: every reducer is pure — no filesystem, network, model calls or module-level mutable state. Malformed *content* yields a verdict, never an exception. A gate returns `None` to pass or a `dict` to fail; `review_gate` additionally may return a `{"warn": ...}` dict.

## Observable behaviour

### `count_of(value)`

Faithful port of `countOf`. Returns the number when `value` is a real number; returns the parsed number when `value` is a non-blank string that JS `Number()` would parse (whitespace-trimmed decimal, so `"3"` → `3`); otherwise returns a not-a-number sentinel — `float("nan")` — which is neither zero nor a valid integer. `None`, `""`, `"   "`, `"three"` and any other type all reach the sentinel. Python's `bool` is *not* a number here: `True` must reach the sentinel, matching JS where `typeof true !== 'number'`. The port must never coerce an absent count to zero (`int(value or 0)` and friends are forbidden) — that is the exact fabrication the upstream comment at task.js lines 80-84 exists to prevent.

The predicate `review_gate` uses must treat `3.0` (from `"3"`) as an integer and `1.5`, `nan` and `True` as not integers, matching `Number.isInteger`.

### `review_gate(review, branch, base_branch)`

Checks in this order, stopping at the first that fires:

1. **Dirty worktree.** `review["porcelain"]`, coerced as JS `String(x || '')` does (any falsy value becomes `""`) and then stripped, is non-empty → `{"blocked": "tests", "detail": ...}`. The detail says the worktree is still dirty, that nothing was pushed, and carries the porcelain text itself so the evidence travels with the verdict. This runs before any count check, even when the counts are also bad.
2. **Unusable counts.** `count_of(review["commitCount"])` or `count_of(review["taggedCount"])` is not an integer → `{"warn": ...}` whose text names the two raw reported values and ends with "Plan-Hash gate skipped". No `blocked` key. A `review` of `None`, `{}` or any non-mapping lands here rather than raising.
3. **Zero commits** → `{"blocked": "implement", "detail": ...}` naming `branch` and `base_branch` ("branch X has no commits on top of Y — implementation produced nothing to ship.").
4. **Untagged commits** (`tagged_count < commit_count`) → `{"blocked": "implement", "detail": ...}` stating "only N of M commits", explaining a future run would read the branch as stale and hard-reset it, that nothing was pushed, and containing the literal "Do NOT re-run this subtask" language.
5. Otherwise `None`. More trailers than commits is a pass, not a failure.

Counts are read off the mapping defensively (reuse `_field`), and keys stay camelCase (`porcelain`, `commitCount`, `taggedCount`) because these dicts come from harness result JSON. Where a detail or warn string embeds a compared value, render it JS-style rather than with Python `repr`, per the `json.dumps` quirk the sibling gate already documents (`True`/`1` render differently under Python equality than in JSON).

### Plan hash

The card names one `plan_hash_gate`; task.js implements two functions and both behaviours must survive. Port them as `is_plan_hash(value)` and `plan_hash_mismatch(impl_hash, review_hash)`, optionally with a thin `plan_hash_gate` wrapper that returns `None` or a verdict dict; the reconciliation decision is the implementer's, but neither behaviour may be dropped or merged away.

- `is_plan_hash(value)`: `True` only for a `str` of exactly 8 lowercase hex characters. Uppercase, 7 or 9 characters, non-hex letters, empty, leading whitespace, `None` and integers are all `False`. Anchor the pattern at both ends (`fullmatch`, or `^...$` with no trailing-newline laxity — Python `$` also matches before a final newline, so `"a1b2c3d4\n"` must not pass).
- `plan_hash_mismatch(impl_hash, review_hash)`: `None` when either hash is not a valid plan hash (a stage that failed to report its hash says nothing about the other; inventing drift there sends someone after a phantom), and `None` when both are valid and equal. When both are valid and differ, returns the detail string naming both hashes, saying the plan's bytes were "modified after implementation", that every trailer on the branch is stale and a future resume would hard-reset the work.

## Error paths

No input shape raises. `review` may be `None`, a non-mapping, or missing any key; counts may be any type; hashes may be any type. Malformed content always produces a verdict, a warning, or `None`. Nothing in this subtask reads the filesystem or shells out, so there are no I/O error paths.

## Tests

All tests below are **pure-function unit tests** per §14 (lines 477-492): `steps/reducers.py` is named there as a pure module whose tests are ported alongside the logic from the `.test.mjs` behavioural spec, with no harness, filesystem or network. They therefore go in the existing flat `tests/steps/test_reducers.py` alongside the sibling gates' tests — *not* in the "Steps" tier (temporary git repo + temporary brd board), which is reserved for code that touches git or brd; these gates touch neither. No adapter, engine or end-to-end tests are in scope.

Ported from `task.test.mjs` lines 59-138 (`reviewGate`):

1. A clean tree with tagged commits proceeds to Ship (`None`).
2. A dirty worktree blocks with `blocked == "tests"`, detail matching "nothing was pushed" and containing the porcelain line.
3. Whitespace-only porcelain (`"\n"`, `"   "`) is a clean tree — git's trailing newline must not block every run.
4. The dirty-tree check runs before the counts: `{"porcelain": "?? new.js", "commitCount": 0, "taggedCount": 0}` yields `blocked == "tests"`.
5. Zero commits → `blocked == "implement"`, detail naming branch and base.
6. `commitCount=3, taggedCount=2` → `blocked == "implement"`, detail matching "only 2 of 3 commits" and "Do NOT re-run this subtask".
7. `commitCount=3, taggedCount=4` → `None`.
8. Unusable counts warn and skip, parametrised over `commitCount` in `None`, `""`, `"three"`, `1.5`; `taggedCount` in absent/`None`, `float("nan")`, `True` — each returns a truthy `warn` matching "Plan-Hash gate skipped", with no `blocked` key.
9. A missing review object (`None`, `{}`, and — Python's stand-in for `undefined` — an absent mapping) warns instead of raising.
10. Numeric-string counts are usable: `"3"`/`"3"` → `None`; `"3"`/`"2"` → `blocked == "implement"`.

Ported from `task.test.mjs` lines 189-219 (`isPlanHash` / `planHashMismatch`):

11. `is_plan_hash` accepts `"a1b2c3d4"` and `"00000000"`; rejects `"A1B2C3D4"`, `"a1b2c3d"`, `"a1b2c3d4e"`, `"a1b2c3g4"`, `""`, `"  a1b2c3d4"`, `None`, `12345678`, and (Python-specific) `"a1b2c3d4\n"`.
12. Matching hashes report no drift (`None`).
13. A hash that changed mid-run returns a detail naming both hashes and matching "modified after implementation".
14. Drift is not claimed when either hash is unusable: `(None, "a1b2c3d4")`, `("a1b2c3d4", "")`, `("not-a-hash", "a1b2c3d4")`, `(None, None)` all return `None`.

Added for the Python port (no JS counterpart, because JS coercion differs):

15. `count_of` direct tests: `3` → `3`; `"3"` → `3`; `" 3 "` → `3`; `None`, `""`, `"   "`, `"three"`, `True`, `[]`, `{}` → a not-a-number result that is neither `0` nor an integer. Explicitly assert `count_of(True)` is not usable and that no input is silently read as zero.

## Verification

`uv run pytest` (the repo's single verification command per CLAUDE.md; no lint or typecheck step exists).

---

# Review Gate, Plan-Hash Gate and `count_of` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `countOf`, `reviewGate`, `isPlanHash` and `planHashMismatch` from `.claude/workflows/task.js`'s PURE region into `src/agent_manager/steps/reducers.py` as pure Python reducers, with their `task.test.mjs` cases as unit tests.

**Architecture:** Three additions appended to the existing `reducers.py` on branch `m1/task-port-the-review-gate-5ee2ee50` (cut from `origin/m1/task-port-the-exploration-ef33352b`, which already contains `verification_gate`, `exploration_output_gate`, `_json` and `_field`): a JS-faithful numeric coercion layer (`count_of` plus private `_js_number`, `_is_integer`, `_js_text`), the five-outcome `review_gate`, and the plan-hash pair `is_plan_hash` / `plan_hash_mismatch` with a thin `plan_hash_gate` wrapper. Nothing else in the file is touched, and no other subtask's code is assumed to exist. All tests land in the existing flat `tests/steps/test_reducers.py` — the §14 pure-function unit tier that the sibling gates' tests already use.

**Tech Stack:** Python 3 stdlib only (`re`, `math`, `json`, `collections.abc.Mapping`), pytest (`uv run pytest`), `uv`-managed project.

**Spec:** `docs/superpowers/specs/task-port-the-review-gate-5ee2ee50-design.md` (reproduced verbatim above); upstream source of truth `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §5, §6, §14.

## Global Constraints

- Work in worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m1/task-port-the-review-gate-5ee2ee50` on branch `m1/task-port-the-review-gate-5ee2ee50`. All paths below are relative to that worktree root.
- Verification command, and the only one: `uv run pytest`. There is no lint or typecheck step.
- Do not modify `verification_gate`, `exploration_output_gate`, `MIN_SUMMARY_LENGTH`, `PLACEHOLDER_SUMMARIES`, or any existing test in `tests/steps/test_reducers.py` — they belong to done sibling `ef33352b`. Append only.
- Every function added here is pure: no filesystem, network, model calls, module-level mutable state. No input shape may raise; malformed content yields a verdict, a warning, or `None`.
- Harness-JSON keys stay camelCase in dict lookups: `porcelain`, `commitCount`, `taggedCount`. Python identifiers stay snake_case.
- Never coerce an absent count to zero. `int(value or 0)`, `int(value)` on untrusted input, and `float(value)` without a guard are forbidden.
- Detail/warn strings are ported byte-for-byte from `task.js` lines 168, 179, 184, 190, 105-107, except that `${...}` interpolations are rendered JS-style via `_js_text` rather than with Python `repr`.

## Review Focus

- Boolean counts: `{"commitCount": True}` must be *unusable* (warn), never read as `1` — `bool` is an `int` subclass in Python but `typeof true !== 'number'` in JS. Test in Task 1 and Task 2.
- Numeric-string counts in the detail text: `"3"`/`"2"` must render "only 2 of 3 commits", not "only 2.0 of 3.0" — `float("3")` is `3.0` and Python `str()` would leak the `.0`. Test in Task 2.
- Python-only numeric spellings: `"1_0"` and `"٣"` (non-ASCII digit) parse under Python `float()` but are `NaN` under JS `Number()`; `"inf"`/`"Infinity"`/`"nan"` and hex forms like `"0x10"` fall outside the decimal-literal grammar `count_of` is scoped to (§ Observable behaviour: "whitespace-trimmed decimal") even though `Number("0x10")` is `16` in real JS — the port deliberately narrows to decimal only, so these must all be unusable. They must be unusable, or a review that reported garbage would be judged as a real count. Test in Task 1.
- Non-string and non-mapping inputs to `review_gate`: `porcelain` of `0`, `False`, `5`, `["?? a"]`, and a `review` of `"x"` or `["x"]` must produce a verdict/warn, never a `TypeError`. Test in Task 2.
- Plan hashes that are almost valid: `"a1b2c3d4\n"` (Python `$` matches before a final newline) and `b"a1b2c3d4"` (bytes, where `re` would raise on a `str` pattern) must be `False`, not accepted or raising. Test in Task 3.

---

### Task 1: `count_of` and the JS numeric layer

**Files:**
- Modify: `src/agent_manager/steps/reducers.py` (append after `_field`, which ends at line 81)
- Test: `tests/steps/test_reducers.py` (append at end of file)

**Interfaces:**
- Consumes: nothing from other tasks. Reuses the existing module's `import json` and `_json` only indirectly.
- Produces:
  - `count_of(value: object) -> float | int` — the number, or `math.nan` when unusable.
  - `_is_integer(value: object) -> bool` — the `Number.isInteger` equivalent; `False` for `bool`, `nan`, `inf`, `1.5`, non-numbers; `True` for `3` and `3.0`.
  - `_js_text(value: object) -> str` — JS `String(x)`-style rendering used in Task 2's messages.
  - `_js_number(text: str) -> float` — private string parser behind `count_of`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_reducers.py`:

```python
# ── count_of ─────────────────────────────────────────────────────────────────
# No JS counterpart: Python's coercions are looser than JS's, so the port needs
# its own pins. task.js lines 79-89 explain why "unusable" must never collapse
# to zero — a Review that reported no count at all would otherwise be judged as
# having found ZERO COMMITS and stop the run blaming Implement.

import math

from agent_manager.steps.reducers import count_of
from agent_manager.steps.reducers import _is_integer, _js_text


@pytest.mark.parametrize(("value", "expected"), [(3, 3), (0, 0), (-2, -2), (1.5, 1.5)])
def test_a_real_number_is_returned_as_is(value, expected):
    assert count_of(value) == expected


@pytest.mark.parametrize("text", ["3", " 3 ", "3.0", "+3", "1e3", "\t3\n"])
def test_a_numeric_string_is_parsed(text):
    assert _is_integer(count_of(text))


def test_a_numeric_string_parses_to_its_value():
    assert count_of("3") == 3
    assert count_of(" 3 ") == 3
    assert count_of("1e3") == 1000


@pytest.mark.parametrize(
    "value", [None, "", "   ", "three", [], {}, object(), b"3", 3j]
)
def test_an_unusable_value_is_not_a_number_and_not_zero(value):
    result = count_of(value)
    assert math.isnan(result)
    assert result != 0
    assert not _is_integer(result)


@pytest.mark.parametrize("value", [True, False])
def test_a_bool_is_not_a_count(value):
    # typeof true !== 'number' in JS, but bool is an int subclass in Python.
    # Reading True as 1 would invent a commit that nobody counted.
    assert math.isnan(count_of(value))
    assert not _is_integer(count_of(value))


@pytest.mark.parametrize("text", ["1_0", "٣", "inf", "Infinity", "nan", "0x10", "1,0"])
def test_a_python_only_numeric_spelling_is_unusable(text):
    # Python's float()/int() accept all of these; JS Number() returns NaN for
    # every one. Accepting them would read garbage as a real count.
    assert math.isnan(count_of(text))


def test_is_integer_matches_number_is_integer():
    assert _is_integer(3)
    assert _is_integer(3.0)
    assert _is_integer(-0.0)
    assert not _is_integer(1.5)
    assert not _is_integer(math.nan)
    assert not _is_integer(math.inf)
    assert not _is_integer(True)
    assert not _is_integer("3")
    assert not _is_integer(None)


def test_js_text_renders_values_the_way_a_template_literal_does():
    assert _js_text(None) == "null"
    assert _js_text(True) == "true"
    assert _js_text(False) == "false"
    assert _js_text("three") == "three"
    assert _js_text("") == ""
    assert _js_text(3) == "3"
    assert _js_text(3.0) == "3"
    assert _js_text(1.5) == "1.5"
    assert _js_text(math.nan) == "NaN"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -k "count_of or is_integer or js_text or bool_is_not or numeric_string or unusable_value or python_only" -v`
Expected: collection error / FAIL with `ImportError: cannot import name 'count_of' from 'agent_manager.steps.reducers'`.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/steps/reducers.py`, add `import math` and `import re` to the import block at the top (it currently reads `import json` then `from collections.abc import Mapping`), so it becomes:

```python
import json
import math
import re
from collections.abc import Mapping
```

Then append after `_field` (which ends at line 81, before `exploration_output_gate`):

```python
# JS `Number()` accepts exactly this grammar for a decimal literal. Python's
# float() is looser — it takes "1_0", "inf", "nan" and non-ASCII digits like
# "٣" — so the string is screened first. [0-9] rather than \d on purpose:
# \d matches Unicode digits that JS would reject.
_JS_DECIMAL = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _js_number(text: str) -> float:
    """Parse ``text`` the way JS ``Number()`` does, or ``nan`` if it would not."""
    body = text.strip()
    if not _JS_DECIMAL.fullmatch(body):
        return math.nan
    return float(body)


def _is_integer(value: object) -> bool:
    """The ``Number.isInteger`` equivalent: a real, finite, whole number."""
    # bool first: it is an int subclass in Python, but not a number in JS.
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()  # False for nan and inf
    return False


def _js_text(value: object) -> str:
    """Render ``value`` as a JS template literal would, not as Python ``repr``.

    ``None`` is "null", not "None"; ``True`` is "true", not "True"; a whole
    float is "3", not "3.0". These strings go into operator-facing details, so
    they must read as the harness JSON the numbers came from.
    """
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, int):
        return str(value)
    return _json(value)


# Number() is too eager to be a validator here: Number(null) and Number('')
# are both 0, so a Review that reported no count at all would be judged as
# having found ZERO COMMITS and the run would stop claiming the implementation
# produced nothing. That is a fabricated fact pinned on the wrong stage. An
# absent count is unusable, not zero — only a real number, or a string holding
# one, counts.
def count_of(value: object) -> float | int:
    """The reported count, or ``nan`` — never zero — when it is unusable."""
    if isinstance(value, bool):
        return math.nan
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and value.strip() != "":
        return _js_number(value)
    return math.nan
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: PASS, including every pre-existing sibling-gate test.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/reducers.py tests/steps/test_reducers.py
git commit -m "feat(reducers): port count_of with a JS-faithful numeric layer"
```

---

### Task 2: `review_gate`

**Files:**
- Modify: `src/agent_manager/steps/reducers.py` (append after `count_of`)
- Test: `tests/steps/test_reducers.py` (append at end of file)

**Interfaces:**
- Consumes: `count_of`, `_is_integer`, `_js_text` from Task 1; the existing `_field(mapping, name) -> object` helper (line 79) which returns `None` for any non-mapping.
- Produces: `review_gate(review: object, branch: object, base_branch: object) -> dict[str, str] | None` — `None` to proceed, `{"blocked": "tests" | "implement", "detail": str}` to stop, `{"warn": str}` when the counts are unusable.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_reducers.py`:

```python
# ── review_gate ──────────────────────────────────────────────────────────────
# Ported from task.test.mjs:59-139. The Review -> Ship boundary: Review REPORTS
# three facts, this gate judges them, before anything is pushed.

from agent_manager.steps.reducers import review_gate

BRANCH = "task-42"
BASE = "main"


def clean(**overrides):
    """A review result that passes every check, with overrides applied."""
    base = {"porcelain": "", "commitCount": 3, "taggedCount": 3}
    base.update(overrides)
    return base


def test_a_clean_tree_with_tagged_commits_proceeds_to_ship():
    assert review_gate(clean(), BRANCH, BASE) is None


def test_a_dirty_worktree_stops_the_run_before_anything_is_pushed():
    gate = review_gate(clean(porcelain=" M src/a.js"), BRANCH, BASE)
    assert gate["blocked"] == "tests"
    assert "nothing was pushed" in gate["detail"]
    # The evidence travels with the verdict.
    assert "M src/a.js" in gate["detail"]


@pytest.mark.parametrize("blank", ["\n", "   ", "\t\n ", ""])
def test_whitespace_only_porcelain_is_a_clean_tree(blank):
    # git prints a trailing newline even when it has nothing to say; treating
    # that as dirt would block every single run.
    assert review_gate(clean(porcelain=blank), BRANCH, BASE) is None


def test_the_dirty_tree_check_runs_before_the_commit_counts():
    # A dirty tree means the counts describe a branch that is missing work, so
    # reporting the count problem first would send someone after the wrong bug.
    gate = review_gate(
        {"porcelain": "?? new.js", "commitCount": 0, "taggedCount": 0}, BRANCH, BASE
    )
    assert gate["blocked"] == "tests"


def test_a_non_string_porcelain_is_coerced_not_raised_on():
    # String(x || '') in JS: falsy becomes "", truthy becomes its text.
    assert review_gate(clean(porcelain=0), BRANCH, BASE) is None
    assert review_gate(clean(porcelain=False), BRANCH, BASE) is None
    assert review_gate(clean(porcelain=None), BRANCH, BASE) is None
    assert review_gate(clean(porcelain=5), BRANCH, BASE)["blocked"] == "tests"
    assert review_gate(clean(porcelain=["?? a"]), BRANCH, BASE)["blocked"] == "tests"


def test_zero_commits_stops_the_run_as_an_implement_failure():
    gate = review_gate(clean(commitCount=0, taggedCount=0), BRANCH, BASE)
    assert gate["blocked"] == "implement"
    assert "task-42 has no commits on top of main" in gate["detail"]


def test_an_untagged_commit_stops_the_run_because_a_later_run_would_hard_reset_it():
    gate = review_gate(clean(commitCount=3, taggedCount=2), BRANCH, BASE)
    assert gate["blocked"] == "implement"
    assert "only 2 of 3 commits" in gate["detail"]
    # Re-running is the destructive move, so the verdict must say so.
    assert "Do NOT re-run this subtask" in gate["detail"]


def test_more_trailers_than_commits_is_not_a_failure():
    # A commit can legitimately carry the trailer twice, or a merge can inflate
    # the count. The gate only cares that nothing is MISSING one.
    assert review_gate(clean(commitCount=3, taggedCount=4), BRANCH, BASE) is None


@pytest.mark.parametrize(
    "bad",
    [
        {"commitCount": None},
        {"commitCount": ""},
        {"commitCount": "three"},
        {"commitCount": 1.5},
        {"taggedCount": None},
        {"taggedCount": float("nan")},
        {"taggedCount": True},
        {"commitCount": True},
        {"commitCount": "1_0"},
        {"taggedCount": []},
    ],
)
def test_unusable_counts_warn_and_skip_rather_than_blocking_or_passing(bad):
    # None and "" are the sharp ones: float() would turn both into 0, which
    # would read as "zero commits" and stop the run blaming Implement for a
    # fact nobody ever measured.
    gate = review_gate(clean(**bad), BRANCH, BASE)
    assert gate["warn"]
    assert "blocked" not in gate
    assert "Plan-Hash gate skipped" in gate["warn"]


def test_the_warn_names_both_raw_reported_values_js_style():
    gate = review_gate({"commitCount": None, "taggedCount": True}, BRANCH, BASE)
    assert "(null/true)" in gate["warn"]


@pytest.mark.parametrize("missing", [None, {}, {"porcelain": ""}, "x", ["x"], 7])
def test_a_missing_or_non_mapping_review_warns_instead_of_raising(missing):
    # Reachability is a property of the caller, and the caller is exactly the
    # thing that changes, so the gate stays independently safe.
    gate = review_gate(missing, BRANCH, BASE)
    assert gate["warn"]
    assert "blocked" not in gate


def test_a_numeric_string_count_is_still_usable():
    # The schema asks for integers, but models do hand back "3". Rejecting that
    # would skip the gate on a branch that could have been checked.
    assert review_gate(clean(commitCount="3", taggedCount="3"), BRANCH, BASE) is None
    gate = review_gate(clean(commitCount="3", taggedCount="2"), BRANCH, BASE)
    assert gate["blocked"] == "implement"


def test_numeric_string_counts_are_rendered_without_a_python_float_tail():
    # float("3") is 3.0; "only 2.0 of 3.0 commits" would read as a bug report
    # about the gate rather than about the branch.
    gate = review_gate(clean(commitCount="3", taggedCount="2"), BRANCH, BASE)
    assert "only 2 of 3 commits" in gate["detail"]


def test_a_zero_count_from_a_numeric_string_still_blocks_as_implement():
    gate = review_gate(clean(commitCount="0", taggedCount="0"), BRANCH, BASE)
    assert gate["blocked"] == "implement"
    assert "no commits on top of" in gate["detail"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -k review_gate -v`
Expected: collection error / FAIL with `ImportError: cannot import name 'review_gate' from 'agent_manager.steps.reducers'`.

- [ ] **Step 3: Write the implementation**

Append to `src/agent_manager/steps/reducers.py`, after `count_of`:

```python
# The Review -> Ship boundary. Review is asked to REPORT three facts and never
# to interpret or act on them: an agent that both measures and judges can talk
# itself out of the judgement. Review is also the last stage that writes, so
# this is the earliest boundary at which the facts can be judged — and judging
# here costs no dispatch, because Ship simply never boots.
#
# Returns None to proceed, {blocked, detail} to stop, or {warn} when Review's
# numbers are unusable and the Plan-Hash half of the gate has to be skipped.
def review_gate(
    review: object,
    branch: object,
    base_branch: object,
) -> dict[str, str] | None:
    """``None`` to proceed, a blocked verdict to stop, or a ``warn`` dict."""
    raw_porcelain = _field(review, "porcelain")
    # Mirrors JS `String((review && review.porcelain) || '')`: any falsy value
    # (absent, None, '', 0, False) becomes the empty string.
    porcelain = ("" if not raw_porcelain else _js_text(raw_porcelain)).strip()
    if len(porcelain) > 0:
        return {
            "blocked": "tests",
            "detail": "worktree still dirty after review, so this subtask's commit "
            "would not contain this work (nothing was pushed):\n" + porcelain,
        }

    # Implement decides RESUME vs RESET by grepping for exactly this trailer, so
    # an untagged commit reads as stale debris and a later run would
    # `reset --hard` it away. Catching that here, before anything is pushed, is
    # the whole point.
    raw_commit = _field(review, "commitCount")
    raw_tagged = _field(review, "taggedCount")
    commit_count = count_of(raw_commit)
    tagged_count = count_of(raw_tagged)
    if not _is_integer(commit_count) or not _is_integer(tagged_count):
        return {
            "warn": "review did not report usable commit/trailer counts "
            f"({_js_text(raw_commit)}/{_js_text(raw_tagged)}) — Plan-Hash gate skipped"
        }
    if commit_count == 0:
        return {
            "blocked": "implement",
            "detail": f"branch {branch} has no commits on top of {base_branch} — "
            "implementation produced nothing to ship.",
        }
    if tagged_count < commit_count:
        return {
            "blocked": "implement",
            "detail": f"only {_js_text(tagged_count)} of {_js_text(commit_count)} "
            f"commits on {branch} carry their Plan-Hash trailer, so a future run "
            "would read this branch as stale and hard-reset it. Nothing was pushed. "
            "Do NOT re-run this subtask until the trailers are added "
            "(interactively, by a human) or the work is otherwise preserved.",
        }
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: PASS, all tests including Task 1's and the sibling gates'.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/reducers.py tests/steps/test_reducers.py
git commit -m "feat(reducers): port review_gate with its five ordered outcomes"
```

---

### Task 3: `is_plan_hash`, `plan_hash_mismatch` and the `plan_hash_gate` wrapper

**Files:**
- Modify: `src/agent_manager/steps/reducers.py` (append after `review_gate`)
- Test: `tests/steps/test_reducers.py` (append at end of file)

**Interfaces:**
- Consumes: the module-level `re` import added in Task 1.
- Produces:
  - `is_plan_hash(value: object) -> bool`
  - `plan_hash_mismatch(impl_hash: object, review_hash: object) -> str | None` — the diagnosis text, or `None`.
  - `plan_hash_gate(impl_hash: object, review_hash: object) -> dict[str, str] | None` — `{"detail": <that text>}` or `None`. It reconciles the card's singular `plan_hash_gate` name with task.js's two functions; task.js only *logs* the drift (line 833), so the wrapper carries no `blocked` key.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_reducers.py`:

```python
# ── is_plan_hash / plan_hash_mismatch ────────────────────────────────────────
# Ported from task.test.mjs:189-220. A Plan-Hash is the first 8 hex characters
# of sha256sum(<plan file>). Implement writes the trailers; Review recomputes
# the hash independently, so comparing the two catches the plan file changing
# mid-run — which silently invalidates every trailer already written.

from agent_manager.steps.reducers import is_plan_hash, plan_hash_gate, plan_hash_mismatch


@pytest.mark.parametrize("good", ["a1b2c3d4", "00000000", "ffffffff", "0123456789abcdef"[:8]])
def test_a_plan_hash_is_exactly_eight_lowercase_hex_characters(good):
    assert is_plan_hash(good) is True


@pytest.mark.parametrize(
    "bad",
    [
        "A1B2C3D4",
        "a1b2c3d",
        "a1b2c3d4e",
        "a1b2c3g4",
        "",
        "  a1b2c3d4",
        "a1b2c3d4 ",
        None,
        12345678,
        b"a1b2c3d4",
        ["a1b2c3d4"],
    ],
)
def test_anything_else_is_not_a_plan_hash(bad):
    assert is_plan_hash(bad) is False


def test_a_trailing_newline_does_not_sneak_a_hash_through():
    # Python's `$` also matches before a final newline, so the pattern must be
    # anchored with fullmatch (or \Z). A hash read straight off a command's
    # stdout is exactly how this gets hit.
    assert is_plan_hash("a1b2c3d4\n") is False


def test_matching_hashes_report_no_drift():
    assert plan_hash_mismatch("a1b2c3d4", "a1b2c3d4") is None


def test_a_hash_that_changed_mid_run_is_named_as_a_modified_plan():
    # The gate downstream will say "0 of 3 commits carry their trailer", which
    # reads as an implementation failure. It is not: the plan moved underneath
    # commits that were correct when written. Only this comparison can say so.
    drift = plan_hash_mismatch("a1b2c3d4", "ffffffff")
    assert "a1b2c3d4" in drift
    assert "ffffffff" in drift
    assert "modified after implementation" in drift
    assert "hard-reset" in drift


@pytest.mark.parametrize(
    ("impl", "review"),
    [
        (None, "a1b2c3d4"),
        ("a1b2c3d4", None),
        ("a1b2c3d4", ""),
        ("not-a-hash", "a1b2c3d4"),
        ("a1b2c3d4", "A1B2C3D4"),
        (None, None),
        (12345678, "a1b2c3d4"),
    ],
)
def test_drift_is_not_claimed_when_either_hash_is_unusable(impl, review):
    # A stage that failed to report its hash tells us nothing about the other
    # one; inventing a mismatch there would send someone after a phantom.
    assert plan_hash_mismatch(impl, review) is None


def test_the_wrapper_returns_none_or_a_detail_verdict():
    assert plan_hash_gate("a1b2c3d4", "a1b2c3d4") is None
    assert plan_hash_gate(None, "a1b2c3d4") is None
    gate = plan_hash_gate("a1b2c3d4", "ffffffff")
    assert gate["detail"] == plan_hash_mismatch("a1b2c3d4", "ffffffff")
    # task.js only logs the drift (line 833); the stop is review_gate's.
    assert "blocked" not in gate
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -k "plan_hash" -v`
Expected: collection error / FAIL with `ImportError: cannot import name 'is_plan_hash' from 'agent_manager.steps.reducers'`.

- [ ] **Step 3: Write the implementation**

Append to `src/agent_manager/steps/reducers.py`, after `review_gate`:

```python
# A Plan-Hash is the first 8 hex characters of sha256sum(<plan file>).
# fullmatch, not match/search with `$`: Python's `$` also matches just before a
# final newline, so `"a1b2c3d4\n"` would slip through a `^...$` pattern.
_PLAN_HASH = re.compile(r"[0-9a-f]{8}")


def is_plan_hash(value: object) -> bool:
    """``True`` only for a ``str`` of exactly 8 lowercase hex characters."""
    return isinstance(value, str) and _PLAN_HASH.fullmatch(value) is not None


# Implement writes the trailers; Review recomputes the hash from the plan file
# independently, which is deliberate — Review is the ground truth a FUTURE run
# will reproduce, so it must never just echo what Implement claimed. Comparing
# the two costs no command and catches the one thing neither stage can see on
# its own: the plan file changing mid-run (ticked checkboxes are the usual
# culprit), which silently invalidates every trailer already written.
def plan_hash_mismatch(impl_hash: object, review_hash: object) -> str | None:
    """The drift diagnosis, or ``None`` when there is nothing trustworthy to say."""
    if not is_plan_hash(impl_hash) or not is_plan_hash(review_hash):
        return None
    if impl_hash == review_hash:
        return None
    return (
        f"plan hash CHANGED mid-run: implement committed trailers as {impl_hash}, "
        f"review recomputed {review_hash} from the same plan file. "
        "The plan's bytes were modified after implementation, so every trailer on "
        "this branch is now stale and a future resume would hard-reset the work. "
        "The Plan-Hash gate below will stop the run; this is why."
    )


def plan_hash_gate(impl_hash: object, review_hash: object) -> dict[str, str] | None:
    """Verdict form of :func:`plan_hash_mismatch`, for the card's singular name.

    Carries no ``blocked`` key on purpose: in `task.js` the drift is *logged*
    as a diagnosis (line 833) and the stop itself comes from
    :func:`review_gate`'s untagged-commit branch.
    """
    detail = plan_hash_mismatch(impl_hash, review_hash)
    return None if detail is None else {"detail": detail}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS, no failures, no errors.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/steps/reducers.py tests/steps/test_reducers.py
git commit -m "feat(reducers): port is_plan_hash, plan_hash_mismatch and the plan_hash_gate wrapper"
```

---

### Task 4: Update the module docstring to cover the new gates

**Files:**
- Modify: `src/agent_manager/steps/reducers.py:1-13` (the module docstring)
- Test: `tests/steps/test_reducers.py` (no new test — docstring-only change, covered by the existing suite still passing)

**Interfaces:**
- Consumes: everything from Tasks 1-3.
- Produces: nothing importable.

- [ ] **Step 1: Edit the docstring**

The docstring currently reads "A gate returns ``None`` to pass, or a verdict ``dict`` to fail." Replace that final sentence so the `warn` outcome and the new line range are documented. The full replacement for lines 1-13:

```python
"""The gates ported faithfully from the `PURE:BEGIN`/`PURE:END` region of
`task.js` (lines 58-194 of the sibling leave-me-alone plugin's
`workflows/task.js`), with its `task.test.mjs` as the behavioural
specification.

Each gate exists because a live run got past it once, and the comments here
record the incident. Every gate is pure — no filesystem, network, model calls
or module-level mutable state. Malformed *content* produces a verdict rather
than an exception; the one shape still required of a caller is
``verification_gate``'s ``suite_cmds``, which must be an actual list (the
engine always has one, and `task.js` throws here too). A gate returns ``None``
to pass, or a verdict ``dict`` to fail — with one exception: ``review_gate``
also returns ``{"warn": ...}`` when Review's counts are unusable, which
proceeds but skips the Plan-Hash half of the check.
"""
```

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/agent_manager/steps/reducers.py
git commit -m "docs(reducers): document review_gate's warn outcome in the module docstring"
```
