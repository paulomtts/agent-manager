<!-- task-pipeline: validated -->
# Port plan_check (card d3feb87e)

## 1. Scope

One module, `src/agent_manager/steps/plan_check.py`, and its tests at `tests/steps/test_plan_check.py`. It ports `find_validated_plan` (the `planCheck` function) and its helpers from the leave-me-alone plugin's `scripts/plan-check.mjs`, plus the boolean gate `has_validated_plan` that the workflow's `when:` clause names. `scripts/plan-check.test.mjs` is the behavioural specification: every test there has a ported equivalent here.

This is a deterministic step (design §4 `steps/` layout line 127, §6 lines 258-260: "The engine calls `run(ctx) -> dict`. No network, no model."). It reads the filesystem and nothing else — no git, no `brd`, no subprocess, no model call.

The step answers exactly one question the pipeline asks before dispatching Spec/Plan/Validate agents: *does this card already have a plan that Validate signed off?* If yes, the workflow phase `plan_check` skips to `implement` (design §5 lines 169-173).

**Out of scope.** The CLI flag parsing of the `.mjs` (`parseArgs`, `--compact`, the stdout JSON envelope) — this is an internal step, not a CLI command, so the `{"ok": true, "data": ...}` envelope (CLAUDE.md) does not apply to its return value. Registry/engine wiring of `plan_check.find_validated_plan` and `plan_check.has_validated_plan` into the workflow YAML belongs to the later workflow/registry effort; this card delivers the pure functions only. Short-id and stem naming stays in `dag.py` (card 01d725d6) and is consumed, never re-derived here. `steps/worktree.py` (card 0816e239, done) and `steps/verify.py` (card 9c3b1ffb, blocked on this one) are untouched. Milestone orchestration and non-Claude harnesses are later milestones.

## 2. Module surface

- `VALIDATED_MARKER = "<!-- task-pipeline: validated -->"` — a module constant, matched by literal substring containment only, **never** by regex. A plan whose prose merely discusses the marker therefore reads as validated. This is a deliberate, documented trade: the failure that matters is mistaking a real marker for prose, and a substring test can never make it. The docstring records the accepted cost.
- `matches_card(filename, card) -> bool` — only names ending in `.md` can match; the stem (name minus `.md`) is split on `-` and its **last** segment must equal the card's short id. Splitting is the whole anchor: `task-deadbeefa32af745.md` must not answer for card `a32af745`, which a "preceding character is not a digit" boundary would have let through, since hex ids may be preceded by hex characters.
- `pick_plan(filenames, card) -> str | None` — the matching names, sorted, last one wins; `None` when nothing matches or the listing is empty/`None`. Newest wins because a re-planned subtask leaves the stale file behind, and the stale file must not decide whether Spec/Plan/Validate re-run. "Newest" is lexicographic on the filename, exactly as the `.mjs` does; filenames are date-prefixed in practice, and no `stat` call is made.
- `find_validated_plan(card, plans_dir=None, *, repo_dir=None, list=list_dir, read=read_file) -> dict` — the step entry point. Returns a plain dict, not a Pydantic model: it crosses no process boundary, matching `worktree.ensure`'s own rationale ("a plain dict, since it crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`)").
- `has_validated_plan(result) -> bool` — the `when:` gate: true only when the result says both `found` and `validated`. Pure; takes the dict `find_validated_plan` returned.
- `list_dir` / `read_file` — the default real-filesystem implementations, injectable via the `list`/`read` parameters. This mirrors the `.mjs`'s own `list = readdir, read = readFile` seam and `worktree.py`'s `GitRunner` callable-injection shape: a real default plus a swappable argument so tests can force errors a real filesystem will not produce on demand.

## 3. Observable behaviour

`find_validated_plan` resolves the plans directory first: the explicit `plans_dir` when given, otherwise `<repo_dir>/.claude/plans`. It then resolves `card` to a short id, dispatching on shape — `dag.short_id` itself only accepts a full UUID string (`src/agent_manager/dag.py`: `short_id(card_id: object) -> str` raises `ValueError` unless `card_id` is a 32-hex-character string once dashes are stripped; it does not accept a mapping, an object, or an already-short id), so `find_validated_plan` does the dispatch itself, never assuming `dag.short_id` does it:

- an 8-lowercase-hex-character string is accepted as-is, with no call into `dag.short_id`;
- any other string is passed to `dag.short_id` (a full card UUID, dashes optional);
- a mapping or an attribute-bearing object has its `id` field read locally — the same read `_field(card, "id")` performs in `dag.py`, reimplemented here rather than imported, since `_field` is a private module-level helper of `dag.py` and not part of its public surface — and the extracted value is then passed to `dag.short_id`.

An uppercase or malformed id — including an 8-character string that is not all-lowercase-hex, or a full id that fails `dag.short_id`'s check — raises `ValueError` rather than quietly matching nothing — accepting it would turn a caller's typo into "no plan found", a gate failing open in the direction that halts a run for a reason that is not true.

It then lists the plans directory, picks the newest matching filename, reads it, and reports:

| situation | result |
| --- | --- |
| plans directory missing/unlistable | `{"found": False, "path": "", "validated": False}` |
| directory listable, no matching plan | `{"found": False, "path": "", "validated": False}` |
| plan found, marker present literally | `{"found": True, "path": <abs path>, "validated": True}` |
| plan found, marker absent | `{"found": True, "path": <abs path>, "validated": False}` |
| plan found, unreadable | `{"found": True, "path": <abs path>, "validated": False, "error": "could not read <path>: <reason>"}` |

`path` is the plans directory joined with the chosen filename; it is `""` and never `None` when nothing was found, so a consumer can format it without a guard.

## 4. Error paths

There are no raised exceptions from I/O. A missing plans directory is a normal answer on a first run, not a failure, and any listing error is treated the same way — the step reports "no plan" and the pipeline plans one. An unreadable but existing plan is different: it is reported as `found` and not `validated`, with an `error` string naming the path and the underlying reason, so the journal says why the run re-planned instead of silently pretending the file was not there. The `error` key is present only in that case.

The only exception raised is `ValueError` from argument validation (bad card id, or neither `plans_dir` nor `repo_dir` given) — raised before any filesystem touch, so a caller bug cannot be mistaken for a first run.

## 5. Tests

All tests below live in `tests/steps/test_plan_check.py` and are **Steps**-tier per design §14 line 482-483. For this module — which uses no git and no board, only the filesystem — the Steps-tier rule reads as: exercise the real code against real temporary directories and files created in pytest's `tmp_path`, doing real I/O, and fake the `list`/`read` callables *only* where a test must force an outcome the real filesystem will not produce on demand. That mirrors the `.mjs` test's own use of `fakeFs` and `test_worktree.py`'s placement docstring. None of these belong in the Pure-functions tier (`dag.py`/`reducers.py` unit tests) even though `matches_card`/`pick_plan` are pure, because they are this Steps module's internals and are asserted alongside it; none belong in the Engine or End-to-end tiers.

1. **Card id resolution** — a full card UUID string, and a card mapping/object exposing `id`, both resolve to the same short id via `dag.short_id`; an already-short lowercase id passes through without calling `dag.short_id`; an uppercase or malformed id (short or full) raises `ValueError`; `plans_dir` defaults to `<repo_dir>/.claude/plans`, and an explicit `plans_dir` overrides it. *(Steps tier; real `tmp_path`, no I/O needed for the raising cases.)*
2. **A plan matches when the short id is its final segment** — `task-write-rows-a32af745.md` and `task-a32af745.md` both match. *(Steps.)*
3. **One card's plan never answers for another** — `task-deadbeefa32af745.md`, `task-rows-a32af746.md` and `task-rows-a32af745-old.md` all fail to match `a32af745`. *(Steps.)*
4. **Only `.md` files match** — `task-rows-a32af745.txt` does not. *(Steps.)*
5. **The newest matching plan wins** — given two date-prefixed plans for the same card in a real `tmp_path` directory, the lexicographically last is chosen; a directory holding only another card's plan yields no match; an empty/`None` listing yields no match. *(Steps; real files on disk for the two-plan case.)*
6. **A missing plans directory is a normal answer, not a failure** — pointing at a `tmp_path` subdirectory that was never created returns `{"found": False, "path": "", "validated": False}` and raises nothing. *(Steps; real absent directory, no fake needed.)*
7. **Validated only when the marker is literally present** — a real plan file containing the marker returns `validated=True`; a sibling plan for another card without it returns `validated=False`. *(Steps; real files.)*
8. **A plan that merely discusses the marker still counts** — a real file whose prose quotes the marker returns `validated=True`, with the test naming this as the accepted cost of a literal, non-regex check. *(Steps; real file.)*
9. **A near-miss marker is not validated** — a file carrying a differently-spelled or differently-spaced marker returns `validated=False`, pinning the constant's exact text. *(Steps; real file.)*
10. **An unreadable plan is found but not validated, and says why** — `found=True`, `validated=False`, and `error` mentions the underlying reason. *(Steps; the one case that fakes `read`, because a real unreadable file cannot be reliably produced, exactly as the `.mjs` test's `fakeFs` does.)*
11. **`has_validated_plan` gates on both flags** — true only for `found and validated`; false for not-found, found-but-unvalidated, and the unreadable-with-`error` result. *(Steps; operates on dicts from the tests above.)*

## 6. Verification

`uv run pytest`. There is no separate lint or typecheck command (CLAUDE.md).

---

# Port plan_check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver `src/agent_manager/steps/plan_check.py` — the deterministic step that answers whether a card already has a Validate-signed-off plan on disk — as a faithful port of the leave-me-alone plugin's `scripts/plan-check.mjs`.

**Architecture:** One module of pure helpers (`matches_card`, `pick_plan`, card-id and plans-dir resolution) wrapped by a single I/O entry point `find_validated_plan`, plus the pure `when:` gate `has_validated_plan`. Filesystem access goes through two injectable callables (`list`, `read`) that default to real implementations, mirroring `worktree.py`'s `GitRunner` seam. Card short ids are never re-derived here: `agent_manager.dag.short_id` owns that, and this module only dispatches on the shape of the `card` argument before calling it.

**Tech Stack:** Python 3 (stdlib `os`, `re`, `pathlib`, `collections.abc`), pytest with `tmp_path`, `uv` for running the suite. No Pydantic here — the return value crosses no process boundary (CLAUDE.md).

**Spec:** `docs/superpowers/specs/task-port-plan-check-d3feb87e-design.md` (reproduced verbatim above)

## Global Constraints

- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md).
- Verification command is exactly `uv run pytest`. There is no separate lint or typecheck command (CLAUDE.md).
- The return value is a plain `dict`, never a Pydantic model: it crosses no process boundary (spec §2, CLAUDE.md, `worktree.ensure`'s own docstring).
- `VALIDATED_MARKER` is matched by literal substring containment only, **never** by regex (spec §2).
- Every test in this plan lands in `tests/steps/test_plan_check.py` — the **Steps** tier (design §14:482-483). Main paths use real `tmp_path` I/O; the `list`/`read` fakes appear only where the real filesystem will not produce the outcome on demand.
- The branch is `m1/task-port-plan-check-d3feb87e`, cut from `origin/m1/task-port-worktree-ensure-0816e239`. `src/agent_manager/dag.py` and `src/agent_manager/steps/worktree.py` already exist on it; `steps/verify.py` does not and must not be created here.
- Never modify `src/agent_manager/dag.py` or `src/agent_manager/steps/worktree.py`.

## Review Focus

Five conditions the spec implies but whose handling its eleven listed tests do not pin. Each has a test added to the task that owns the code.

1. **A plan file that is not valid UTF-8** (binary or mis-encoded). `read_file` decodes text, so this raises `UnicodeDecodeError` — a `ValueError`, *not* an `OSError` — and a `except OSError` alone would let it escape the step and crash a run that the spec says has "no raised exceptions from I/O". Expected: the unreadable-plan answer (`found=True`, `validated=False`, `error`). Test in Task 5.
2. **`plans_dir` pointing at an existing regular file, not a directory.** `os.listdir` raises `NotADirectoryError`; spec §4 says "any listing error is treated the same way". Expected: `{"found": False, "path": "", "validated": False}`. Test in Task 5.
3. **A *directory* inside the plans dir whose name matches the card** (e.g. `task-rows-a32af745.md/`). It appears in the listing and wins `pick_plan`, then reading it raises `IsADirectoryError`. Expected: the unreadable-plan answer, not a crash. Test in Task 5.
4. **Neither `plans_dir` nor `repo_dir` given.** Spec §4 names this as the second `ValueError` case, raised before any filesystem touch, but no listed test covers it. Expected: `ValueError`, and no `list`/`read` call. Test in Task 3.
5. **An uppercase *full* UUID.** Spec §3's normative rule is that a full id raises only when it "fails `dag.short_id`'s check" — and `dag.short_id` accepts `[0-9a-fA-F]{32}` and lowercases, so `A32AF745-...` is accepted and normalised, while the uppercase *short* id `A32AF745` raises. §5's test-1 shorthand "(short or full)" reads the other way; §3 governs. Both halves get pinned. Test in Task 3.

---

### Task 1: The marker constant and `matches_card`

**Files:**
- Create: `src/agent_manager/steps/plan_check.py`
- Create: `tests/steps/test_plan_check.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `VALIDATED_MARKER: str` (module constant) and `matches_card(filename: object, card: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/steps/test_plan_check.py` with exactly this content:

```python
"""Behaviour of the plan-check step (design §4 `steps/`, spec card d3feb87e).

Placement follows design §14: `plan_check.py` is a Steps component, so its
behaviour is exercised against real temporary directories and files created in
`tmp_path` -- no network, and no faking of the filesystem except where a test
must force an outcome a real filesystem will not produce on demand (an
unreadable file), exactly as the ported `plan-check.test.mjs` uses its `fakeFs`.
"""

from agent_manager.steps import plan_check
from agent_manager.steps.plan_check import VALIDATED_MARKER, matches_card


def test_the_marker_is_the_exact_literal_validate_writes():
    assert VALIDATED_MARKER == "<!-- task-pipeline: validated -->"


def test_a_plan_matches_when_the_short_id_is_its_final_segment():
    assert matches_card("task-write-rows-a32af745.md", "a32af745") is True
    assert matches_card("task-a32af745.md", "a32af745") is True


def test_one_cards_plan_never_answers_for_another():
    # The hazard the old non-digit boundary could not express: hex ids may be
    # preceded by hex characters.
    assert matches_card("task-deadbeefa32af745.md", "a32af745") is False
    assert matches_card("task-rows-a32af746.md", "a32af745") is False
    assert matches_card("task-rows-a32af745-old.md", "a32af745") is False


def test_only_md_files_match():
    assert matches_card("task-rows-a32af745.txt", "a32af745") is False
    assert matches_card("task-rows-a32af745", "a32af745") is False


def test_a_missing_or_non_string_filename_matches_nothing():
    assert matches_card(None, "a32af745") is False
    assert plan_check.matches_card("", "a32af745") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_manager.steps.plan_check'` at import time.

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/steps/plan_check.py`:

```python
"""Does this card already have a plan that Validate signed off?

A deterministic step (design §4 `steps/`, §6 "The engine calls `run(ctx) -> dict`.
No network, no model."): it reads the filesystem and nothing else -- no git, no
`brd`, no subprocess, no model call. Ported from `planCheck` in the
leave-me-alone plugin's `scripts/plan-check.mjs`, whose `plan-check.test.mjs` is
the behavioural specification.

The agent this replaces did `ls` plus `grep` and cost a dispatch every run. The
answer decides one thing: whether the workflow's `plan_check` phase skips
straight to `implement` (design §5 lines 169-173).
"""

VALIDATED_MARKER = "<!-- task-pipeline: validated -->"
"""What Validate writes into a plan it has signed off.

Matched by literal substring containment, NEVER by regex. A plan whose prose
merely discusses the marker therefore reads as validated: that is the accepted
cost of a check that can never mistake a real marker for prose, which is the
failure that matters.
"""

_MD = ".md"


def matches_card(filename: object, card: str) -> bool:
    """Whether `filename` is a plan file belonging to short id `card`.

    The short id must be the LAST dash-delimited segment of the stem, so a
    longer hex run ending in the same eight characters cannot match. The old
    check tested that the preceding character was not a digit, which is the
    wrong anchor for hex -- `deadbeefa32af745` would have slipped through it.
    """
    name = "" if filename is None else str(filename)
    if not name.endswith(_MD):
        return False
    stem = name[: -len(_MD)]
    return stem.split("-")[-1] == card
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/plan_check.py tests/steps/test_plan_check.py
git commit -m "feat(plan-check): add VALIDATED_MARKER and matches_card"
```

---

### Task 2: `pick_plan` — newest matching plan wins

**Files:**
- Modify: `src/agent_manager/steps/plan_check.py`
- Modify: `tests/steps/test_plan_check.py`

**Interfaces:**
- Consumes: `matches_card(filename: object, card: str) -> bool` from Task 1.
- Produces: `pick_plan(filenames: object, card: str) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_plan_check.py`:

```python
def test_the_newest_matching_plan_wins():
    # A re-planned subtask leaves the old file behind; the stale one must not
    # decide whether Spec/Plan/Validate re-run. "Newest" is lexicographic on
    # the date-prefixed filename -- no `stat` call is made.
    assert (
        pick_plan(
            [
                "2026-01-task-rows-a32af745.md",
                "2026-08-task-rows-a32af745.md",
                "task-other-deadbeef.md",
            ],
            "a32af745",
        )
        == "2026-08-task-rows-a32af745.md"
    )


def test_pick_plan_is_none_when_nothing_matches():
    assert pick_plan(["task-other-deadbeef.md"], "a32af745") is None
    assert pick_plan([], "a32af745") is None
    assert pick_plan(None, "a32af745") is None
```

Add `pick_plan` to the existing `from agent_manager.steps.plan_check import ...` line so it reads:

```python
from agent_manager.steps.plan_check import VALIDATED_MARKER, matches_card, pick_plan
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: FAIL — `ImportError: cannot import name 'pick_plan' from 'agent_manager.steps.plan_check'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `src/agent_manager/steps/plan_check.py`:

```python
def pick_plan(filenames: object, card: str) -> str | None:
    """The newest of the plan files matching `card`, or `None`.

    Newest is the lexicographically last name, exactly as the `.mjs` does:
    plan filenames are date-prefixed in practice, so sorting them is a date
    order, and no `stat` call is needed. A missing or empty listing is no
    match rather than an error.
    """
    if filenames is None:
        return None
    hits = sorted(name for name in filenames if matches_card(name, card))
    return hits[-1] if hits else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/plan_check.py tests/steps/test_plan_check.py
git commit -m "feat(plan-check): add pick_plan, newest matching plan wins"
```

---

### Task 3: Card id and plans directory resolution

**Files:**
- Modify: `src/agent_manager/steps/plan_check.py`
- Modify: `tests/steps/test_plan_check.py`
- Read only: `src/agent_manager/dag.py:23-30` (`short_id`), `:49-53` (`_field`, private — reimplemented, not imported)

**Interfaces:**
- Consumes: `agent_manager.dag.short_id(card_id: object) -> str`, which raises `ValueError` unless `card_id` is a string of 32 hex characters once dashes are stripped, and lowercases its answer.
- Produces: `_card_short_id(card: object) -> str` and `_plans_dir(plans_dir: object | None, repo_dir: object | None) -> str`, both raising `ValueError` on bad input before any filesystem touch. Task 4 calls both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_plan_check.py`:

```python
CARD_UUID = "a32af745-0322-4a49-9dd9-44630af9632d"


def test_an_already_short_lowercase_id_passes_straight_through():
    assert plan_check._card_short_id("a32af745") == "a32af745"


def test_a_full_uuid_resolves_through_dag_short_id():
    assert plan_check._card_short_id(CARD_UUID) == "a32af745"
    assert plan_check._card_short_id(CARD_UUID.replace("-", "")) == "a32af745"
    # dag.short_id accepts either case in a full id and lowercases it, so an
    # uppercase FULL uuid is normalised rather than rejected (spec §3).
    assert plan_check._card_short_id(CARD_UUID.upper()) == "a32af745"


def test_a_card_mapping_or_object_resolves_via_its_id_field():
    class Card:
        id = CARD_UUID

    assert plan_check._card_short_id({"id": CARD_UUID}) == "a32af745"
    assert plan_check._card_short_id(Card()) == "a32af745"


def test_an_uppercase_or_malformed_card_id_raises_rather_than_matching_nothing():
    # Accepting it would turn a caller's typo into "no plan found" -- a gate
    # failing open in the direction that halts a run for a reason that is not
    # true.
    for bad in ["A32AF745", "42", "zzzzzzzz", "", "not-a-uuid", None, 42]:
        with pytest.raises(ValueError):
            plan_check._card_short_id(bad)
    with pytest.raises(ValueError):
        plan_check._card_short_id({"title": "no id here"})


def test_the_plans_dir_defaults_under_the_repo_and_an_explicit_one_wins():
    assert plan_check._plans_dir(None, "/abs/repo") == "/abs/repo/.claude/plans"
    assert plan_check._plans_dir("/p", "/abs/repo") == "/p"
    assert plan_check._plans_dir(Path("/p"), None) == "/p"


def test_neither_plans_dir_nor_repo_dir_is_a_caller_bug_not_a_first_run():
    with pytest.raises(ValueError, match="plans_dir"):
        plan_check._plans_dir(None, None)
```

Add these imports at the top of the test file, after the module docstring and before the `from agent_manager...` imports:

```python
from pathlib import Path

import pytest
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: FAIL — `AttributeError: module 'agent_manager.steps.plan_check' has no attribute '_card_short_id'`.

- [ ] **Step 3: Write the minimal implementation**

Add these imports at the top of `src/agent_manager/steps/plan_check.py`, immediately after the module docstring:

```python
import re
from collections.abc import Mapping
from pathlib import Path

from agent_manager.dag import short_id
```

Then append to the module:

```python
_SHORT_ID = re.compile(r"^[0-9a-f]{8}$")


def _card_short_id(card: object) -> str:
    """The card's eight-character short id, whatever shape the card arrives in.

    `dag.short_id` owns the derivation and is never duplicated here; this only
    dispatches on shape, because `short_id` accepts a full UUID string and
    nothing else -- not a mapping, not an object, not an already-short id.
    Anything it rejects raises `ValueError` here too, before any filesystem
    touch: a typo must not read as "no plan found".
    """
    if isinstance(card, str):
        if _SHORT_ID.match(card):
            return card
        return short_id(card)
    # The same read `dag._field` performs, reimplemented rather than imported:
    # `_field` is private to that module and not part of its public surface.
    raw = card.get("id") if isinstance(card, Mapping) else getattr(card, "id", None)
    return short_id(raw)


def _plans_dir(plans_dir: object | None, repo_dir: object | None) -> str:
    """The directory to list: the explicit one, else `<repo_dir>/.claude/plans`."""
    if plans_dir is not None:
        return str(plans_dir)
    if repo_dir is None:
        raise ValueError(
            "plan_check.find_validated_plan needs plans_dir or repo_dir, got neither"
        )
    return str(Path(repo_dir) / ".claude" / "plans")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/plan_check.py tests/steps/test_plan_check.py
git commit -m "feat(plan-check): resolve card ids via dag.short_id and default the plans dir"
```

---

### Task 4: `find_validated_plan` over a real filesystem

**Files:**
- Modify: `src/agent_manager/steps/plan_check.py`
- Modify: `tests/steps/test_plan_check.py`

**Interfaces:**
- Consumes: `matches_card`, `pick_plan` (Tasks 1-2), `_card_short_id`, `_plans_dir` (Task 3), `VALIDATED_MARKER` (Task 1).
- Produces: `list_dir(path: str) -> list[str]`, `read_file(path: str) -> str`, and `find_validated_plan(card: object, plans_dir: object | None = None, *, repo_dir: object | None = None, list: DirLister = list_dir, read: FileReader = read_file) -> dict[str, object]` returning keys `found: bool`, `path: str`, `validated: bool` (plus `error: str` only in Task 5's unreadable case). Also the aliases `DirLister = Callable[[str], list[str]]` and `FileReader = Callable[[str], str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_plan_check.py`:

```python
def _plans(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """A real plans directory on disk holding `files` (filename -> content).

    Takes a plain dict rather than `**kwargs`: plan filenames contain `-` and
    `.`, neither of which is legal in a Python keyword.
    """
    directory = tmp_path / ".claude" / "plans"
    directory.mkdir(parents=True)
    for name, body in (files or {}).items():
        (directory / name).write_text(body, encoding="utf-8")
    return directory


def test_a_missing_plans_directory_is_a_normal_answer_not_a_failure(tmp_path: Path):
    # First run: nobody has planned anything yet. Real absent directory, no fake.
    got = plan_check.find_validated_plan("a32af745", repo_dir=tmp_path)
    assert got == {"found": False, "path": "", "validated": False}


def test_a_listable_directory_with_no_matching_plan_finds_nothing(tmp_path: Path):
    directory = _plans(tmp_path, {"task-rows-deadbeef.md": "# plan"})
    got = plan_check.find_validated_plan("a32af745", directory)
    assert got == {"found": False, "path": "", "validated": False}


def test_validated_only_when_the_marker_is_literally_present(tmp_path: Path):
    directory = _plans(
        tmp_path,
        {
            "task-rows-a32af745.md": f"# plan\n{VALIDATED_MARKER}\nsteps",
            "task-rows-deadbeef.md": "# plan\nno marker",
        },
    )
    validated = plan_check.find_validated_plan("a32af745", directory)
    assert validated == {
        "found": True,
        "path": str(directory / "task-rows-a32af745.md"),
        "validated": True,
    }
    assert plan_check.find_validated_plan("deadbeef", directory) == {
        "found": True,
        "path": str(directory / "task-rows-deadbeef.md"),
        "validated": False,
    }


def test_a_plan_that_merely_discusses_the_marker_still_counts(tmp_path: Path):
    # Documented deliberately: the check is a substring test, never a regex. A
    # plan quoting the marker in prose reads as validated. That is the accepted
    # cost of never mistaking a real marker for prose -- the failure that
    # matters.
    directory = _plans(
        tmp_path,
        {
            "task-rows-a32af745.md": (
                f"explains that {VALIDATED_MARKER} means signed off"
            )
        },
    )
    assert plan_check.find_validated_plan("a32af745", directory)["validated"] is True


def test_a_near_miss_marker_is_not_validated(tmp_path: Path):
    directory = _plans(
        tmp_path,
        {
            "task-rows-a32af745.md": (
                "<!-- task-pipeline: Validated -->\n"
                "<!--task-pipeline: validated-->\n"
                "<!-- task_pipeline: validated -->\n"
            )
        },
    )
    assert plan_check.find_validated_plan("a32af745", directory)["validated"] is False


def test_the_newest_plan_on_disk_decides(tmp_path: Path):
    directory = _plans(
        tmp_path,
        {
            "2026-01-task-rows-a32af745.md": f"# old\n{VALIDATED_MARKER}",
            "2026-08-task-rows-a32af745.md": "# re-planned, not yet signed off",
        },
    )
    got = plan_check.find_validated_plan("a32af745", directory)
    assert got["path"] == str(directory / "2026-08-task-rows-a32af745.md")
    assert got["validated"] is False


def test_the_card_may_arrive_as_a_uuid_or_a_mapping(tmp_path: Path):
    directory = _plans(
        tmp_path, {"task-rows-a32af745.md": f"# plan\n{VALIDATED_MARKER}"}
    )
    assert plan_check.find_validated_plan(CARD_UUID, directory)["validated"] is True
    assert (
        plan_check.find_validated_plan({"id": CARD_UUID}, directory)["validated"]
        is True
    )


def test_the_repo_dir_default_finds_the_plans_under_dot_claude(tmp_path: Path):
    _plans(tmp_path, {"task-rows-a32af745.md": f"{VALIDATED_MARKER}\n"})
    got = plan_check.find_validated_plan("a32af745", repo_dir=tmp_path)
    assert got["path"] == str(tmp_path / ".claude" / "plans" / "task-rows-a32af745.md")
    assert got["validated"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: FAIL — `AttributeError: module 'agent_manager.steps.plan_check' has no attribute 'find_validated_plan'`.

- [ ] **Step 3: Write the minimal implementation**

Extend the import block at the top of `src/agent_manager/steps/plan_check.py` so it reads:

```python
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path

from agent_manager.dag import short_id
```

Append to the module:

```python
DirLister = Callable[[str], list[str]]
"""Takes a directory path, returns its entry names, raises `OSError` if it cannot."""

FileReader = Callable[[str], str]
"""Takes a file path, returns its text, raises `OSError` if it cannot."""


def list_dir(path: str) -> list[str]:
    """The default `DirLister`: the real entry names of a real directory."""
    return os.listdir(path)


def read_file(path: str) -> str:
    """The default `FileReader`: the real UTF-8 text of a real file."""
    return Path(path).read_text(encoding="utf-8")


def find_validated_plan(
    card: object,
    plans_dir: object | None = None,
    *,
    repo_dir: object | None = None,
    list: DirLister = list_dir,
    read: FileReader = read_file,
) -> dict[str, object]:
    """Whether `card` already has a plan on disk, and whether Validate signed it.

    The deterministic phase's result (design §6) -- a plain dict, since it
    crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`).
    `path` is `""` and never `None` when nothing was found, so a consumer can
    format it without a guard.

    `list` and `read` default to the real filesystem and exist to be swapped in
    tests, the same callable-injection seam `worktree.py` uses for git and the
    ported `.mjs` uses for `readdir`/`readFile`.
    """
    card_id = _card_short_id(card)
    directory = _plans_dir(plans_dir, repo_dir)

    try:
        entries = list(directory)
    except OSError:
        # No plans directory is a normal answer on a first run, not a failure.
        return {"found": False, "path": "", "validated": False}

    name = pick_plan(entries, card_id)
    if name is None:
        return {"found": False, "path": "", "validated": False}

    path = os.path.join(directory, name)
    content = read(path)
    return {"found": True, "path": path, "validated": VALIDATED_MARKER in content}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: PASS (21 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/plan_check.py tests/steps/test_plan_check.py
git commit -m "feat(plan-check): add find_validated_plan over the real filesystem"
```

---

### Task 5: The unreadable-plan path

**Files:**
- Modify: `src/agent_manager/steps/plan_check.py`
- Modify: `tests/steps/test_plan_check.py`

**Interfaces:**
- Consumes: `find_validated_plan(...)` from Task 4, and its `read: FileReader` parameter.
- Produces: the fourth result key `error: str`, present **only** when the chosen plan could not be read, formatted `f"could not read {path}: {reason}"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_plan_check.py`:

```python
def test_an_unreadable_plan_is_found_but_not_validated_and_says_why(tmp_path: Path):
    # The one case that fakes `read`: a reliably unreadable regular file cannot
    # be produced on demand (a test may run as root), exactly why the ported
    # `plan-check.test.mjs` reaches for its `fakeFs` here and nowhere else.
    directory = _plans(tmp_path, {"task-rows-a32af745.md": "# plan"})

    def refuse(path: str) -> str:
        raise PermissionError(13, "EACCES: permission denied")

    got = plan_check.find_validated_plan("a32af745", directory, read=refuse)
    assert got["found"] is True
    assert got["validated"] is False
    assert got["path"] == str(directory / "task-rows-a32af745.md")
    assert "EACCES" in got["error"]
    assert str(directory / "task-rows-a32af745.md") in got["error"]


def test_a_plan_that_is_not_valid_utf8_is_unreadable_not_a_crash(tmp_path: Path):
    # Decoding raises UnicodeDecodeError -- a ValueError, not an OSError -- so
    # catching OSError alone would let a corrupt file crash the run, which spec
    # §4 forbids ("no raised exceptions from I/O").
    directory = _plans(tmp_path)
    (directory / "task-rows-a32af745.md").write_bytes(b"\xff\xfe\x00plan")

    got = plan_check.find_validated_plan("a32af745", directory)
    assert got["found"] is True
    assert got["validated"] is False
    assert "could not read" in got["error"]


def test_a_directory_named_like_a_plan_is_unreadable_not_a_crash(tmp_path: Path):
    directory = _plans(tmp_path)
    (directory / "task-rows-a32af745.md").mkdir()

    got = plan_check.find_validated_plan("a32af745", directory)
    assert got["found"] is True
    assert got["validated"] is False
    assert "could not read" in got["error"]


def test_a_plans_dir_that_is_a_file_reports_no_plan(tmp_path: Path):
    not_a_directory = tmp_path / "plans"
    not_a_directory.write_text("this is a file", encoding="utf-8")

    got = plan_check.find_validated_plan("a32af745", not_a_directory)
    assert got == {"found": False, "path": "", "validated": False}


def test_a_successful_read_carries_no_error_key(tmp_path: Path):
    directory = _plans(tmp_path, {"task-rows-a32af745.md": VALIDATED_MARKER})
    assert "error" not in plan_check.find_validated_plan("a32af745", directory)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: FAIL — `PermissionError: [Errno 13] EACCES: permission denied` and `UnicodeDecodeError` / `IsADirectoryError` propagate out of `find_validated_plan` instead of being reported.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/plan_check.py`, replace the last two lines of `find_validated_plan`:

```python
    path = os.path.join(directory, name)
    content = read(path)
    return {"found": True, "path": path, "validated": VALIDATED_MARKER in content}
```

with:

```python
    path = os.path.join(directory, name)
    try:
        content = read(path)
    except (OSError, UnicodeDecodeError) as exc:
        # Different from a missing directory: the plan IS there, so the journal
        # must say why the run re-planned instead of pretending it was not.
        # UnicodeDecodeError is a ValueError, not an OSError, so a corrupt or
        # binary plan needs naming here or it escapes as a crash.
        return {
            "found": True,
            "path": path,
            "validated": False,
            "error": f"could not read {path}: {exc}",
        }
    return {"found": True, "path": path, "validated": VALIDATED_MARKER in content}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: PASS (26 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/plan_check.py tests/steps/test_plan_check.py
git commit -m "feat(plan-check): report an unreadable plan as found-but-unvalidated"
```

---

### Task 6: `has_validated_plan`, the `when:` gate

**Files:**
- Modify: `src/agent_manager/steps/plan_check.py`
- Modify: `tests/steps/test_plan_check.py`

**Interfaces:**
- Consumes: the `dict` shape `find_validated_plan` returns (Tasks 4-5).
- Produces: `has_validated_plan(result: object) -> bool` — the pure gate the workflow's `when:` clause names (design §5 lines 169-173).

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_plan_check.py`:

```python
def test_the_gate_wants_both_found_and_validated(tmp_path: Path):
    directory = _plans(
        tmp_path,
        {
            "task-rows-a32af745.md": f"# plan\n{VALIDATED_MARKER}",
            "task-rows-deadbeef.md": "# plan\nno marker",
        },
    )
    signed_off = plan_check.find_validated_plan("a32af745", directory)
    unsigned = plan_check.find_validated_plan("deadbeef", directory)
    absent = plan_check.find_validated_plan("cafed00d", directory)

    assert plan_check.has_validated_plan(signed_off) is True
    assert plan_check.has_validated_plan(unsigned) is False
    assert plan_check.has_validated_plan(absent) is False


def test_the_gate_is_closed_for_an_unreadable_plan(tmp_path: Path):
    directory = _plans(tmp_path, {"task-rows-a32af745.md": VALIDATED_MARKER})

    def refuse(path: str) -> str:
        raise PermissionError(13, "EACCES: permission denied")

    unreadable = plan_check.find_validated_plan("a32af745", directory, read=refuse)
    assert plan_check.has_validated_plan(unreadable) is False


def test_the_gate_is_closed_for_anything_that_is_not_a_result_dict():
    # A phase that failed before producing a result must not read as "skip to
    # implement": the gate fails closed, so the pipeline re-plans.
    assert plan_check.has_validated_plan(None) is False
    assert plan_check.has_validated_plan({}) is False
    assert plan_check.has_validated_plan("found") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: FAIL — `AttributeError: module 'agent_manager.steps.plan_check' has no attribute 'has_validated_plan'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `src/agent_manager/steps/plan_check.py`:

```python
def has_validated_plan(result: object) -> bool:
    """The workflow's `when:` gate: skip to `implement` (design §5 lines 169-173).

    Pure -- it only reads the dict `find_validated_plan` returned. Both flags
    are required, and anything that is not a result mapping reads as closed: a
    phase that failed before producing a result must re-plan, never skip.
    """
    if not isinstance(result, Mapping):
        return False
    return bool(result.get("found")) and bool(result.get("validated"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: PASS (29 tests).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS — every pre-existing test (`tests/test_dag.py`, `tests/steps/test_worktree.py`, `tests/steps/test_reducers.py`, and the rest) still green alongside the new file.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/steps/plan_check.py tests/steps/test_plan_check.py
git commit -m "feat(plan-check): add the has_validated_plan when: gate"
```
