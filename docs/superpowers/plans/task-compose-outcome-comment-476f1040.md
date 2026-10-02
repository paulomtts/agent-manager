<!-- task-pipeline: validated -->
# Compose outcome comment bodies (card 476f1040)

Narrows `docs/superpowers/specs/2026-09-29-board-comments-design.md` (B2, B3, B4, B5) and plan `docs/superpowers/plans/2026-09-29-board-comments.md` Task 1.3 to one subtask. Parent: 60189137 (Plumbing). Note: the upstream exploration summary was truncated at 8000 chars (its brief over-ran mid-reference list); nothing below depends on the missing tail, which was only further file:line pointers — all constraints here were re-checked against the spec, plan and source.

## Scope

Create `src/agent_manager/comments.py` (pure: no I/O, no `brd`, no store, no clock) and `tests/test_comments.py`. Both are new files.

In scope: `CAP`, `Comment`, `key`, the five `compose_*` functions, `agent_reason`, and the private helpers they need (escaping, quoting, capping).

Out of scope (owned elsewhere — do not touch): `board.py` `comment_add`/`comment_list` (sibling 3fb36324); `store.py` outbox table and methods (sibling 2e63c3d8); `comments.enqueue`/`comments.flush` (Task 2.1); wiring `compose_*` into `orchestrate.py`/`bases.py`/`cli.py` (Tasks 2.2/2.3); setting the author `am` (the caller of `board.comment_add`). Also out per the story: leave-me-alone orchestrator comments, feeding comments into prompts, deleting/superseding comments, auto-opened issues.

## Interface

```python
CAP = 1500

@dataclass(frozen=True)
class Comment:
    card_id: str
    key: str
    body: str

def key(run_id: str, card_id: str, event: str) -> str
def compose_done(*, run_id, card_id, summary, branch, resumed_at: str | None) -> Comment
def compose_escalated(*, run_id, card_id, token, failed_phase, detail, reason: str | None) -> Comment
def compose_cancelled(*, run_id, card_id, before_phase, branch, relaunch: str) -> Comment
def compose_base_failed(*, run_id, story_id, base_branch, detail) -> Comment
def compose_run_end(*, run_id, milestone_id, token, payload: Mapping[str, Any]) -> Comment
def agent_reason(results: Mapping[str, Any], failed_phase: str) -> str | None
```

`Comment` is a frozen dataclass (internal state, not a validated boundary file, per CLAUDE.md).

## Observable behavior

Keys (B5): `key(run, card, event)` returns `f"{run}/{card}/{event}"`. Events per composer: done → `done`; escalated → `escalated:<token>`; cancelled → `cancelled`; base failed → `base-failed` (card is `story_id`); run end → `run-end:<token>` (card is `milestone_id`). `done`/`cancelled` carry no token; `escalated`/`run-end` are lease-token-scoped. `Comment.card_id` is the card the comment goes on (subtask, story, or milestone respectively).

Body format (B4), every composer:
- First line exactly `am · <outcome> · run <run-id>` (outcome: `done`, `escalated`, `cancelled`, `base failed`, and for run end the run's outcome from `payload` — `done` / `escalated` / `paused` / `cancelled`).
- Commands (resume, `am logs`, relaunch, next) rendered in backticks.
- Real card ids rendered as `[[id]]`.
- Agent text appears only inside double quotes, with every `[[` rewritten to `[ [`.
- Last line exactly `am-key: <key>`.
- `len(body) <= CAP`. When over, agent text is cut first and `… (truncated; see \`am logs <run> <card> --phase <p>\`)` is appended; the first line and the `am-key` line always survive intact.

Content per event (B2):
- done: outcome; `(resumed at <phase>)` only when `resumed_at` is not None; branch; commit count; Plan-Hash; spec and plan paths; verify commands passed; review findings fixed — drawn from `summary` (a `SubtaskSummary`, whose `results` is keyed by phase name). No agent text.
- escalated: phase; gate/detail; the capped quoted `reason` when not None; `next:` resume command; `why:` `am logs` command for this run/card/phase.
- cancelled: phase it stopped before; branch keeping the work; relaunch command (`relaunch`).
- base failed: base branch; detail.
- run end: outcome, run id, done N of M, the escalated card (as `[[id]]`) and phase when any, parked cards, integrated branch or Integrate failure, the next command — all read from `payload`.

`agent_reason(results, failed_phase)` returns exactly one field, read from `results[failed_phase]`: `validate_spec`/`validate_plan` → `reason` (`CriticResult`, results.py:58-60); `implement` → `blocked_reason` (`ImplementResult`, results.py:100); `review` → `unresolved_blockers` (`ReviewResult`, results.py:117, a `list[str]`, joined into one string). Any other phase, a missing phase entry, or an empty/falsy value → `None`. It never returns any other field (no summaries, reports, stdout).

## Error paths

These are pure functions with no failure I/O. `agent_reason` does not raise on a missing phase key or missing field — it returns `None`. Oversized agent text is truncated, never rejected. Escaping is applied before capping so a cut cannot leave an unescaped `[[`.

## Tests

All tests are plain unit tests in `tests/test_comments.py` — tier "pure functions get plain unit tests colocated by module" (agent-manager design §14 Testing; board-comments design §6 Testing). No fixtures, no temp git repo, no temp board, no `e2e` marker.

1. Golden body: done, fresh (no `(resumed at …)` line). Tier: pure unit.
2. Golden body: done, resumed (`(resumed at review)` present). Tier: pure unit.
3. Golden body: escalated with a reason (quoted, `next:` and `why:` backticked commands). Tier: pure unit.
4. Golden body: escalated without a reason (no quoted text). Tier: pure unit.
5. Golden body: cancelled. Tier: pure unit.
6. Golden body: base failed (card is the story). Tier: pure unit.
7. Golden bodies: run end for outcomes done, escalated, paused, cancelled (card is the milestone; escalated card rendered `[[id]]`). Tier: pure unit.
8. A 10,000-char reason is cut: `len(body) <= CAP`, the truncation marker with the `am logs` command is present, the first line and final `am-key: <key>` line are intact. Tier: pure unit.
9. Agent text `see [[4f31e025-…]]` renders as `[ [4f31e025-…]]` and the body contains no `[[` other than real card ids (plan Review Focus 1). Tier: pure unit.
10. Keys per event match B5: `<run>/<card>/done`, `…/escalated:<token>`, `…/cancelled`, `…/base-failed`, `…/run-end:<token>`; two different tokens give two different escalated keys. Tier: pure unit.
11. `agent_reason`: returns `reason` for `validate_spec` and `validate_plan`, `blocked_reason` for `implement`, joined `unresolved_blockers` for `review`; `None` for another phase (e.g. `verify`), a missing phase entry, `None`/empty string, and an empty list. Tier: pure unit.

---

# Compose outcome comment bodies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pure `src/agent_manager/comments.py` that turns each run outcome (done, escalated, cancelled, merged base failed, run end) into a capped, escaped, keyed `Comment` body, plus `agent_reason` to pull the one allowed agent failure field.

**Architecture:** One new module with no I/O. A shared private `_render` builds every body: the fixed first line, code-authored lines, an optional quoted agent-text line, more code-authored lines, and the fixed `am-key:` last line. It enforces `CAP` by cutting the agent text first, then the code-authored lines before the quote. Composers read `SubtaskSummary.results` (values are decoded pydantic models or plain dicts, `runtime/context.py:45-60`) and run payloads (`orchestrate.py:160-275`, `1568-1582`) through a tolerant `_field` reader that never raises.

**Tech Stack:** Python 3.12, stdlib `dataclasses`, pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-compose-outcome-comment-476f1040-design.md` (copied above), narrowing `docs/superpowers/specs/2026-09-29-board-comments-design.md` B2–B5.

**Upstream note:** both the spec author's summary (2128 chars, capped at 2000) and the exploration summary (8632 chars, capped at 8000) came to this stage truncated, which means those stages ran past their length briefs. This plan does not rely on either summary. It was written from the spec file on disk and from the source files cited below.

**Branch / worktree:** `m12/task-compose-outcome-comment-476f1040` at `/home/paulomtts/Code/agent-manager/.claude/worktrees/m12/task-compose-outcome-comment-476f1040`, cut from `m12/task-add-the-board-comments-2e63c3d8`. Nothing here depends on `board.comment_add`/`comment_list` (sibling 3fb36324) or on the store outbox (sibling 2e63c3d8); this module imports neither.

## Global Constraints

- `CAP = 1500` characters per body; `len(body) <= CAP` always.
- First line exactly `am · <outcome> · run <run-id>` (the `·` is U+00B7, surrounded by single spaces).
- Last line exactly `am-key: <key>`, key `<run-id>/<card-id>/<event>`, event one of `done`, `escalated:<lease-token>`, `cancelled`, `base-failed`, `run-end:<lease-token>`.
- Commands in backticks. Real card ids as `[[id]]`. Agent text inside double quotes with every `[[` rewritten to `[ [`.
- Truncation marker exactly `… (truncated; see \`<command>\`)` (`…` is U+2026). For escalated the command is `am logs <run> <card> --phase <p>`; composers with no agent phase use `am status <run>`.
- No agent text on the board except `CriticResult.reason`, `ImplementResult.blocked_reason`, `ReviewResult.unresolved_blockers`, read only through `agent_reason`. `ImplementResult.report`, `ReviewResult.fix_summary`, `ReviewResult.findings` text and verify output tails never appear (findings appear only as a count).
- Pure: no I/O, no `brd`, no store, no clock. Do not touch `board.py`, `store.py`, `orchestrate.py`, `bases.py`, `cli.py`.
- `Comment` is a frozen dataclass (CLAUDE.md: internal state).
- Verification: `uv run pytest` green, whole suite.

## Review Focus

1. **Agent text with three or more `[` in a row (`[[[x]]]`)** — one non-overlapping `str.replace` leaves `[ [[x]]]`, which still opens a link. Expected: `[ [ [x]]]`, with no `[[` anywhere in the body. Test in Task 2.
2. **An over-cap reason made entirely of `[[`** — the cut lands inside escaped text. Expected: body `<= CAP` and still no `[[`, because escaping runs before the cut. Test in Task 2.
3. **Oversized code-authored text with no agent text (a 5,000-char gate `detail`)** — no quote is left to cut. Expected: body `<= CAP`, first line, `next:`/`why:` lines and the `am-key` line intact, truncation marker present. Test in Task 2.
4. **`results` entries that are plain dicts or garbage (a string) instead of pydantic models** — after a checkpoint round-trip or a hand-built summary. Expected: `agent_reason` reads dicts the same way and returns `None` for garbage, never raising. Test in Task 1.
5. **A cancelled run whose lanes also escalated (`controlled_payload` with `escalations`)** — the escalated card sits in a list, not at top level. Expected: the run-end comment still names `[[subtask]] at <phase>`, the outcome stays `cancelled`, and next is the relaunch command. Test in Task 4.

---

### Task 1: `CAP`, `Comment`, `key` and `agent_reason`

**Files:**
- Create: `src/agent_manager/comments.py`
- Test: `tests/test_comments.py` (create; flat `tests/` layout like `tests/test_dag.py`, `tests/test_results.py`)

**Interfaces:**
- Consumes: `agent_manager.results.CriticResult`, `ImplementResult`, `ReviewResult` (`src/agent_manager/results.py:55-122`), in tests only.
- Produces: `CAP: int = 1500`; `ELLIPSIS: str = "…"`; `Comment(card_id: str, key: str, body: str)` frozen dataclass; `key(run_id: str, card_id: str, event: str) -> str`; `agent_reason(results: Mapping[str, Any], failed_phase: str) -> str | None`; private `_field(value: Any, name: str) -> Any` (mapping `.get` or `getattr(..., None)`), used by Tasks 3 and 4.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_comments.py` with exactly this content (the header imports and helpers serve every later task too):

```python
"""Outcome comment bodies (board-comments design B2-B5): pure unit tests."""

import dataclasses

import pytest

from agent_manager import comments
from agent_manager.results import (
    CriticResult,
    ImplementResult,
    PlanResult,
    ReviewResult,
    SpecResult,
)
from agent_manager.runtime.walk import SubtaskSummary

RUN = "r1"
CARD = "card-476f1040"
STORY = "story-60189137"
MILESTONE = "ms-21f4cf06"
TOKEN = "tok-1"


def _review(*, findings=("a", "b"), blockers=()):
    return ReviewResult(
        findings=list(findings),
        unresolved_blockers=list(blockers),
        fix_summary="fixed both",
        porcelain="",
        commit_count=3,
        tagged_count=1,
        plan_hash="abcd1234",
    )


def _implement(*, blocked_reason=None):
    return ImplementResult(
        blocked=blocked_reason is not None,
        blocked_reason=blocked_reason,
        resumed=False,
        plan_hash="abcd1234",
        report="did it",
    )


def test_cap_is_1500():
    assert comments.CAP == 1500


def test_comment_is_frozen():
    comment = comments.Comment(card_id=CARD, key="k", body="b")
    with pytest.raises(dataclasses.FrozenInstanceError):
        comment.body = "x"


def test_key_joins_run_card_and_event():
    assert comments.key(RUN, CARD, "done") == "r1/card-476f1040/done"
    assert comments.key(RUN, CARD, "escalated:tok-1") == "r1/card-476f1040/escalated:tok-1"


def test_agent_reason_reads_the_critic_reason_for_both_validate_phases():
    critic = CriticResult(blockers=True, reason="spec misses the error path", summary="blocked")
    assert comments.agent_reason({"validate_spec": critic}, "validate_spec") == "spec misses the error path"
    assert comments.agent_reason({"validate_plan": critic}, "validate_plan") == "spec misses the error path"


def test_agent_reason_reads_blocked_reason_for_implement():
    results = {"implement": _implement(blocked_reason="plan step 3 contradicts the spec")}
    assert comments.agent_reason(results, "implement") == "plan step 3 contradicts the spec"


def test_agent_reason_joins_unresolved_blockers_for_review():
    results = {"review": _review(blockers=("missing test", "wrong key"))}
    assert comments.agent_reason(results, "review") == "missing test; wrong key"


def test_agent_reason_reads_plain_mappings_too():
    assert comments.agent_reason({"implement": {"blocked_reason": "x"}}, "implement") == "x"
    assert comments.agent_reason({"review": {"unresolved_blockers": ["a", "b"]}}, "review") == "a; b"


@pytest.mark.parametrize(
    ("results", "phase"),
    [
        ({"verify": {"passed": False, "detail": "red"}}, "verify"),
        ({}, "implement"),
        ({"implement": _implement(blocked_reason=None)}, "implement"),
        ({"validate_spec": CriticResult(blockers=True, reason="", summary="s")}, "validate_spec"),
        ({"validate_plan": CriticResult(blockers=True, reason=None, summary="s")}, "validate_plan"),
        ({"review": _review(blockers=())}, "review"),
        ({"implement": "not a result"}, "implement"),
    ],
    ids=["other-phase", "missing-entry", "none", "empty-string", "none-reason", "empty-list", "garbage"],
)
def test_agent_reason_is_none_without_a_failure_field(results, phase):
    assert comments.agent_reason(results, phase) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_comments.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'comments' from 'agent_manager'` (the module does not exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `src/agent_manager/comments.py` with exactly this content:

```python
"""Outcome comment bodies for brd cards (board-comments design B2-B5).

Pure: no I/O, no `brd`, no store, no clock. Each `compose_*` turns one
outcome's data into a `Comment` that a caller later enqueues and flushes
(Task 2.1); this module never posts anything. Agent text enters only
through `agent_reason`'s three failure fields, quoted, `[[`-escaped and
cut first when a body would exceed `CAP`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_manager.runtime.walk import SubtaskSummary

CAP = 1500
"""Hard cap on one comment body, in characters (B4)."""

ELLIPSIS = "…"


@dataclass(frozen=True)
class Comment:
    """One composed outcome comment. Internal state, so a dataclass (CLAUDE.md)."""

    card_id: str
    key: str
    body: str


def key(run_id: str, card_id: str, event: str) -> str:
    """The idempotency key `<run-id>/<card-id>/<event>` (B5)."""
    return f"{run_id}/{card_id}/{event}"


_REASON_FIELDS = {
    "validate_spec": "reason",
    "validate_plan": "reason",
    "implement": "blocked_reason",
    "review": "unresolved_blockers",
}
"""The only agent-written field each failing phase may put on the board (B3)."""


def _field(value: Any, name: str) -> Any:
    """`name` off a mapping or an object, or None. Never raises.

    `SubtaskSummary.results` values are decoded pydantic models or plain
    dicts (`runtime/context.decode`); run payloads are plain dicts. Anything
    else reads as absent.
    """
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def agent_reason(results: Mapping[str, Any], failed_phase: str) -> str | None:
    """The agent's own explanation of `failed_phase`'s failure, or None.

    Exactly one field per phase (`_REASON_FIELDS`); review's list of
    unresolved blockers is joined with `"; "`. Another phase, a missing
    entry, or an empty value gives None.
    """
    name = _REASON_FIELDS.get(failed_phase)
    if name is None:
        return None
    value = _field(results.get(failed_phase), name)
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(item) for item in value if item)
    if not isinstance(value, str) or not value.strip():
        return None
    return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_comments.py -v`
Expected: all tests PASS (14 collected: 7 single tests plus 7 parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/comments.py tests/test_comments.py
git commit -m "feat(comments): comment keys and the agent failure reason"
```

---

### Task 2: The capped renderer and `compose_escalated`

**Files:**
- Modify: `src/agent_manager/comments.py` (append after `agent_reason`)
- Test: `tests/test_comments.py` (append)

**Interfaces:**
- Consumes: `key`, `Comment`, `CAP`, `ELLIPSIS` from Task 1.
- Produces: `compose_escalated(*, run_id: str, card_id: str, token: str, failed_phase: str, detail: str | None, reason: str | None) -> Comment`; private `_unlink(text: str) -> str`, `_cmd(command: str) -> str`, `_render(first: str, before: Sequence[str], last: str, *, see: str, quote: tuple[str, str] | None = None, after: Sequence[str] = ()) -> str`, all used by Tasks 3 and 4.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_comments.py`:

```python
def _escalated(*, reason, detail="review blockers: 1 unresolved", phase="review", token=TOKEN):
    return comments.compose_escalated(
        run_id=RUN,
        card_id=CARD,
        token=token,
        failed_phase=phase,
        detail=detail,
        reason=reason,
    )


def test_escalated_with_a_reason_golden_body():
    comment = _escalated(reason="tests do not cover the empty list")
    assert comment.card_id == CARD
    assert comment.key == "r1/card-476f1040/escalated:tok-1"
    assert comment.body == "\n".join(
        [
            "am · escalated · run r1",
            "phase: review",
            "detail: review blockers: 1 unresolved",
            'reason: "tests do not cover the empty list"',
            "next: `am resume r1`",
            "why: `am logs r1 card-476f1040 --phase review`",
            "am-key: r1/card-476f1040/escalated:tok-1",
        ]
    )


def test_escalated_without_a_reason_has_no_quoted_text():
    comment = _escalated(reason=None)
    assert comment.body == "\n".join(
        [
            "am · escalated · run r1",
            "phase: review",
            "detail: review blockers: 1 unresolved",
            "next: `am resume r1`",
            "why: `am logs r1 card-476f1040 --phase review`",
            "am-key: r1/card-476f1040/escalated:tok-1",
        ]
    )
    assert '"' not in comment.body


def test_escalated_without_a_detail_omits_the_detail_line():
    comment = _escalated(reason=None, detail=None)
    assert "detail:" not in comment.body
    assert comment.body.split("\n")[1] == "phase: review"


def test_escalated_keys_are_lease_token_scoped():
    first = _escalated(reason=None, token="tok-1")
    second = _escalated(reason=None, token="tok-2")
    assert first.key == "r1/card-476f1040/escalated:tok-1"
    assert second.key == "r1/card-476f1040/escalated:tok-2"
    assert first.key != second.key
    assert second.body.split("\n")[-1] == "am-key: r1/card-476f1040/escalated:tok-2"


def test_a_10000_char_reason_is_cut_first_and_the_key_line_survives():
    comment = _escalated(reason="x" * 10_000, detail="implement blocked", phase="implement")
    lines = comment.body.split("\n")
    assert len(comment.body) <= comments.CAP
    assert lines[0] == "am · escalated · run r1"
    assert lines[1] == "phase: implement"
    assert lines[2] == "detail: implement blocked"
    assert lines[3].startswith('reason: "xxxx')
    assert lines[3].endswith(
        '" … (truncated; see `am logs r1 card-476f1040 --phase implement`)'
    )
    assert lines[3].count("x") > 1000
    assert lines[4] == "next: `am resume r1`"
    assert lines[5] == "why: `am logs r1 card-476f1040 --phase implement`"
    assert lines[-1] == "am-key: r1/card-476f1040/escalated:tok-1"


def test_agent_card_refs_are_broken_so_they_create_no_backlink():
    comment = _escalated(reason="see [[4f31e025-aaaa]] for context")
    assert 'reason: "see [ [4f31e025-aaaa]] for context"' in comment.body
    assert "[[" not in comment.body


def test_runs_of_three_brackets_are_fully_broken():
    comment = _escalated(reason="[[[x]]]")
    assert 'reason: "[ [ [x]]]"' in comment.body
    assert "[[" not in comment.body


def test_an_over_cap_reason_of_only_brackets_stays_escaped_after_the_cut():
    comment = _escalated(reason="[[" * 5000)
    assert len(comment.body) <= comments.CAP
    assert "[[" not in comment.body
    assert comment.body.split("\n")[-1] == "am-key: r1/card-476f1040/escalated:tok-1"


def test_an_over_cap_detail_with_no_reason_is_cut_and_keeps_the_commands():
    comment = _escalated(reason=None, detail="d" * 5000, phase="implement")
    lines = comment.body.split("\n")
    assert len(comment.body) <= comments.CAP
    assert lines[0] == "am · escalated · run r1"
    assert lines[-4] == "… (truncated; see `am logs r1 card-476f1040 --phase implement`)"
    assert lines[-3] == "next: `am resume r1`"
    assert lines[-2] == "why: `am logs r1 card-476f1040 --phase implement`"
    assert lines[-1] == "am-key: r1/card-476f1040/escalated:tok-1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_comments.py -v`
Expected: the 9 new tests FAIL with `AttributeError: module 'agent_manager.comments' has no attribute 'compose_escalated'`; Task 1's tests still PASS.

- [ ] **Step 3: Write minimal implementation**

Append to the end of `src/agent_manager/comments.py`:

```python
def _unlink(text: str) -> str:
    """`text` with every `[[` broken to `[ [`, so it cannot create a ref (B4).

    Repeated until none is left: `str.replace` is non-overlapping, so one
    pass turns `[[[` into `[ [[`, which still opens a link.
    """
    while "[[" in text:
        text = text.replace("[[", "[ [")
    return text


def _cmd(command: str) -> str:
    """A command as the board shows it: in backticks (B4)."""
    return f"`{command}`"


def _render(
    first: str,
    before: Sequence[str],
    last: str,
    *,
    see: str,
    quote: tuple[str, str] | None = None,
    after: Sequence[str] = (),
) -> str:
    """One body of at most `CAP` characters (B4).

    Lines are `first`, `before`, the quoted agent text (`quote` is
    `(label, text)`), `after`, `last`. Agent text is escaped before anything
    is cut, then cut first, with the truncation marker naming `see`. If cutting
    it to nothing is still too long, the agent line is dropped and the
    joined `before` lines are cut instead. `first`, `after` and `last` are
    never cut.
    """
    marker = f"{ELLIPSIS} (truncated; see {_cmd(see)})"
    if quote is not None:
        label, text = quote
        text = _unlink(text)
        body = "\n".join([first, *before, f'{label}: "{text}"', *after, last])
        if len(body) <= CAP:
            return body
        empty = "\n".join([first, *before, f'{label}: "" {marker}', *after, last])
        room = CAP - len(empty)
        if room > 0:
            cut = f'{label}: "{text[:room]}" {marker}'
            return "\n".join([first, *before, cut, *after, last])
    else:
        body = "\n".join([first, *before, *after, last])
        if len(body) <= CAP:
            return body
    kept = "\n".join([first, marker, *after, last])
    room = CAP - len(kept) - 1
    head = "\n".join(before)[: max(room, 0)]
    return "\n".join([first, head, marker, *after, last])


def compose_escalated(
    *,
    run_id: str,
    card_id: str,
    token: str,
    failed_phase: str,
    detail: str | None,
    reason: str | None,
) -> Comment:
    """The subtask's escalation: phase, detail, the agent's reason, what next (B2)."""
    comment_key = key(run_id, card_id, f"escalated:{token}")
    logs = f"am logs {run_id} {card_id} --phase {failed_phase}"
    before = [f"phase: {failed_phase}"]
    if detail:
        before.append(f"detail: {_unlink(detail)}")
    body = _render(
        f"am · escalated · run {run_id}",
        before,
        f"am-key: {comment_key}",
        see=logs,
        quote=("reason", reason) if reason else None,
        after=[f"next: {_cmd(f'am resume {run_id}')}", f"why: {_cmd(logs)}"],
    )
    return Comment(card_id=card_id, key=comment_key, body=body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_comments.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/comments.py tests/test_comments.py
git commit -m "feat(comments): capped, escaped escalation comments"
```

---

### Task 3: `compose_done`, `compose_cancelled`, `compose_base_failed`

**Files:**
- Modify: `src/agent_manager/comments.py` (append after `compose_escalated`)
- Test: `tests/test_comments.py` (append)

**Interfaces:**
- Consumes: `key`, `Comment`, `_field` (Task 1); `_render`, `_unlink`, `_cmd` (Task 2). `SubtaskSummary` (`src/agent_manager/runtime/walk.py:242-255`, `results: dict[str, Any]` keyed by phase name). Phase result shapes it reads: `review` → `ReviewResult.commit_count`, `.findings`; `implement` → `ImplementResult.plan_hash`, falling back to `docs_commit` → `{"plan_hash": str}` (`steps/docs_commit.py:198,219`); `spec` → `SpecResult.path`; `plan` → `PlanResult.path`, falling back to `plan_check` → `{"found", "path", "validated"}` (`steps/plan_check.py:141-161`) when planning was skipped; `verify` → `{"passed", "verified": [{"command", "ok", "tail"}], "detail"}` (`steps/verify.py:282-319`).
- Produces: `compose_done(*, run_id: str, card_id: str, summary: SubtaskSummary, branch: str, resumed_at: str | None) -> Comment`; `compose_cancelled(*, run_id: str, card_id: str, before_phase: str | None, branch: str, relaunch: str) -> Comment`; `compose_base_failed(*, run_id: str, story_id: str, base_branch: str, detail: str | None) -> Comment`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_comments.py`:

```python
_VERIFY = {
    "passed": True,
    "verified": [{"command": "uv run pytest", "ok": True, "tail": "5 passed"}],
    "detail": "",
}


def _done_summary():
    return SubtaskSummary(
        results={
            "spec": SpecResult(path="docs/superpowers/specs/s.md", note=None),
            "plan": PlanResult(path="docs/superpowers/plans/p.md", self_reviewed=True, note=None),
            "implement": _implement(),
            "review": _review(),
            "verify": _VERIFY,
        }
    )


def _done(*, summary=None, resumed_at=None):
    return comments.compose_done(
        run_id=RUN,
        card_id=CARD,
        summary=summary if summary is not None else _done_summary(),
        branch="m12/task-x-476f1040",
        resumed_at=resumed_at,
    )


def test_done_fresh_golden_body():
    comment = _done()
    assert comment.card_id == CARD
    assert comment.key == "r1/card-476f1040/done"
    assert comment.body == "\n".join(
        [
            "am · done · run r1",
            "branch: m12/task-x-476f1040",
            "commits: 3",
            "Plan-Hash: abcd1234",
            "spec: docs/superpowers/specs/s.md",
            "plan: docs/superpowers/plans/p.md",
            "verified: `uv run pytest`",
            "review findings fixed: 2",
            "am-key: r1/card-476f1040/done",
        ]
    )


def test_done_resumed_golden_body():
    comment = _done(resumed_at="review")
    assert comment.key == "r1/card-476f1040/done"
    assert comment.body == "\n".join(
        [
            "am · done · run r1",
            "(resumed at review)",
            "branch: m12/task-x-476f1040",
            "commits: 3",
            "Plan-Hash: abcd1234",
            "spec: docs/superpowers/specs/s.md",
            "plan: docs/superpowers/plans/p.md",
            "verified: `uv run pytest`",
            "review findings fixed: 2",
            "am-key: r1/card-476f1040/done",
        ]
    )


def test_done_carries_no_agent_text():
    body = _done().body
    for agent_text in ("did it", "fixed both", "5 passed", '"'):
        assert agent_text not in body


def test_done_after_a_skipped_planning_reads_the_found_plan_and_docs_commit_hash():
    summary = SubtaskSummary(
        results={
            "plan_check": {"found": True, "path": ".claude/plans/p.md", "validated": True},
            "docs_commit": {"plan_hash": "feedbeef"},
            "review": _review(),
            "verify": _VERIFY,
        }
    )
    assert _done(summary=summary).body == "\n".join(
        [
            "am · done · run r1",
            "branch: m12/task-x-476f1040",
            "commits: 3",
            "Plan-Hash: feedbeef",
            "plan: .claude/plans/p.md",
            "verified: `uv run pytest`",
            "review findings fixed: 2",
            "am-key: r1/card-476f1040/done",
        ]
    )


def test_cancelled_golden_body():
    comment = comments.compose_cancelled(
        run_id=RUN,
        card_id=CARD,
        before_phase="implement",
        branch="m12/task-x-476f1040",
        relaunch="am run --milestone ms-21f4cf06",
    )
    assert comment.card_id == CARD
    assert comment.key == "r1/card-476f1040/cancelled"
    assert comment.body == "\n".join(
        [
            "am · cancelled · run r1",
            "stopped before: implement",
            "branch: m12/task-x-476f1040",
            "relaunch: `am run --milestone ms-21f4cf06`",
            "am-key: r1/card-476f1040/cancelled",
        ]
    )


def test_base_failed_golden_body_goes_on_the_story():
    comment = comments.compose_base_failed(
        run_id=RUN,
        story_id=STORY,
        base_branch="m12/base-story-60189137",
        detail="merge conflict in src/x.py",
    )
    assert comment.card_id == STORY
    assert comment.key == "r1/story-60189137/base-failed"
    assert comment.body == "\n".join(
        [
            "am · base failed · run r1",
            "base branch: m12/base-story-60189137",
            "detail: merge conflict in src/x.py",
            "am-key: r1/story-60189137/base-failed",
        ]
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_comments.py -v`
Expected: the 6 new tests FAIL with `AttributeError: module 'agent_manager.comments' has no attribute 'compose_done'` (or `compose_cancelled` / `compose_base_failed`); earlier tests still PASS.

- [ ] **Step 3: Write minimal implementation**

Append to the end of `src/agent_manager/comments.py`:

```python
def compose_done(
    *,
    run_id: str,
    card_id: str,
    summary: SubtaskSummary,
    branch: str,
    resumed_at: str | None,
) -> Comment:
    """The subtask's done comment: facts only, no agent text (B2).

    Each line is present only when its source result is: a resumed or
    plan-skipping walk may lack `spec`/`plan`, so the plan path falls back to
    `plan_check`'s found plan and the Plan-Hash to `docs_commit`'s.
    """
    results = summary.results
    review = results.get("review")
    lines: list[str] = []
    if resumed_at is not None:
        lines.append(f"(resumed at {resumed_at})")
    lines.append(f"branch: {branch}")
    commits = _field(review, "commit_count")
    if commits is not None:
        lines.append(f"commits: {commits}")
    plan_hash = _field(results.get("implement"), "plan_hash") or _field(
        results.get("docs_commit"), "plan_hash"
    )
    if plan_hash:
        lines.append(f"Plan-Hash: {plan_hash}")
    spec_path = _field(results.get("spec"), "path")
    if spec_path:
        lines.append(f"spec: {spec_path}")
    plan_path = _field(results.get("plan"), "path") or _field(results.get("plan_check"), "path")
    if plan_path:
        lines.append(f"plan: {plan_path}")
    rows = _field(results.get("verify"), "verified") or []
    verified = [str(_field(row, "command")) for row in rows if _field(row, "ok")]
    if verified:
        lines.append("verified: " + ", ".join(_cmd(command) for command in verified))
    findings = _field(review, "findings")
    if findings is not None:
        lines.append(f"review findings fixed: {len(findings)}")
    comment_key = key(run_id, card_id, "done")
    body = _render(
        f"am · done · run {run_id}", lines, f"am-key: {comment_key}", see=f"am status {run_id}"
    )
    return Comment(card_id=card_id, key=comment_key, body=body)


def compose_cancelled(
    *,
    run_id: str,
    card_id: str,
    before_phase: str | None,
    branch: str,
    relaunch: str,
) -> Comment:
    """A subtask a cancel left `in_progress`: where it stopped, its branch, how to relaunch (B2)."""
    lines: list[str] = []
    if before_phase is not None:
        lines.append(f"stopped before: {before_phase}")
    lines.append(f"branch: {branch}")
    lines.append(f"relaunch: {_cmd(relaunch)}")
    comment_key = key(run_id, card_id, "cancelled")
    body = _render(
        f"am · cancelled · run {run_id}", lines, f"am-key: {comment_key}", see=f"am status {run_id}"
    )
    return Comment(card_id=card_id, key=comment_key, body=body)


def compose_base_failed(
    *,
    run_id: str,
    story_id: str,
    base_branch: str,
    detail: str | None,
) -> Comment:
    """A merged base that failed to build, on the story it roots (B2)."""
    lines = [f"base branch: {base_branch}"]
    if detail:
        lines.append(f"detail: {_unlink(detail)}")
    comment_key = key(run_id, story_id, "base-failed")
    body = _render(
        f"am · base failed · run {run_id}", lines, f"am-key: {comment_key}", see=f"am status {run_id}"
    )
    return Comment(card_id=story_id, key=comment_key, body=body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_comments.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/comments.py tests/test_comments.py
git commit -m "feat(comments): done, cancelled and base-failed comments"
```

---

### Task 4: `compose_run_end`

**Files:**
- Modify: `src/agent_manager/comments.py` (append after `compose_base_failed`)
- Test: `tests/test_comments.py` (append)

**Interfaces:**
- Consumes: `key`, `Comment`, `_field` (Task 1); `_render`, `_unlink`, `_cmd` (Task 2). Payload shapes as `run_milestone` builds them: `controlled_payload` (`orchestrate.py:160-193`: `paused`/`cancelled`, `run_id`, `stopped` rows `{story, subtask, before_phase}`, `completed`, `pending`, `resume`, optional `escalations` rows `{level, story, subtask, failed_phase, detail}`); `escalated_payload` (`orchestrate.py:196-241`: `escalated`, `subtask`, `failed_phase`, `detail`, optional `stopped`, `completed`, optional `control`); `integrate_escalated_payload` (`orchestrate.py:258-275`: `escalated`, `phase`, `story`, `files`, `detail`); the done payload (`orchestrate.py:1570-1581`: `done`, `completed`, `integrated: {branch, ...}`). Payload contract for the later wiring card (Task 2.2): it adds `total: int` (the milestone's subtask count) for "done N of M"; without it the line reads `done: N`.
- Produces: `compose_run_end(*, run_id: str, milestone_id: str, token: str, payload: Mapping[str, Any]) -> Comment`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_comments.py`:

```python
def _run_end(payload):
    return comments.compose_run_end(
        run_id=RUN, milestone_id=MILESTONE, token=TOKEN, payload=payload
    )


_RUN_END_KEY = "am-key: r1/ms-21f4cf06/run-end:tok-1"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "done": True,
                "run_id": RUN,
                "completed": ["sub-1", "sub-2", "sub-3"],
                "total": 3,
                "integrated": {"branch": "m12-integrate", "worktree": "/w", "merged": [], "resolved": []},
                "warnings": [],
            },
            [
                "am · done · run r1",
                "done: 3 of 3",
                "integrated: m12-integrate",
                "next: `git merge m12-integrate`",
            ],
        ),
        (
            {
                "escalated": True,
                "run_id": RUN,
                "level": 0,
                "story": "st-1",
                "subtask": "sub-1",
                "failed_phase": "review",
                "detail": "review blockers: 1 unresolved",
                "stopped": [{"story": "st-2", "subtask": "sub-2", "before_phase": "implement"}],
                "completed": ["sub-0"],
                "total": 4,
                "warnings": [],
            },
            [
                "am · escalated · run r1",
                "done: 1 of 4",
                "escalated: [[sub-1]] at review",
                "parked: [[sub-2]]",
                "next: `am resume r1`",
            ],
        ),
        (
            {
                "paused": True,
                "run_id": RUN,
                "stopped": [{"story": "st-2", "subtask": "sub-2", "before_phase": "implement"}],
                "completed": ["sub-0", "sub-1"],
                "pending": ["st-3"],
                "resume": "am resume r1",
                "total": 5,
                "warnings": [],
            },
            [
                "am · paused · run r1",
                "done: 2 of 5",
                "parked: [[sub-2]]",
                "next: `am resume r1`",
            ],
        ),
        (
            {
                "cancelled": True,
                "run_id": RUN,
                "stopped": [
                    {"story": "st-2", "subtask": "sub-2", "before_phase": "verify"},
                    {"story": "st-3", "subtask": None, "before_phase": None},
                ],
                "completed": [],
                "pending": [],
                "total": 2,
                "warnings": [],
            },
            [
                "am · cancelled · run r1",
                "done: 0 of 2",
                "parked: [[sub-2]], [[st-3]]",
                "next: `am run --milestone ms-21f4cf06`",
            ],
        ),
    ],
    ids=["done", "escalated", "paused", "cancelled"],
)
def test_run_end_golden_bodies(payload, expected):
    comment = _run_end(payload)
    assert comment.card_id == MILESTONE
    assert comment.key == "r1/ms-21f4cf06/run-end:tok-1"
    assert comment.body == "\n".join([*expected, _RUN_END_KEY])


def test_run_end_reports_an_integrate_failure():
    payload = {
        "escalated": True,
        "phase": "verify",
        "story": None,
        "files": [],
        "detail": "verification failed: uv run pytest",
        "run_id": RUN,
        "completed": ["sub-1", "sub-2", "sub-3"],
        "total": 3,
        "warnings": [],
    }
    assert _run_end(payload).body == "\n".join(
        [
            "am · escalated · run r1",
            "done: 3 of 3",
            "integrate failed at verify: verification failed: uv run pytest",
            "next: `am run --milestone ms-21f4cf06`",
            _RUN_END_KEY,
        ]
    )


def test_run_end_names_the_story_an_integrate_conflict_stopped_at():
    payload = {
        "escalated": True,
        "phase": "resolve",
        "story": "st-2",
        "files": ["src/x.py"],
        "detail": "conflict in src/x.py",
        "run_id": RUN,
        "completed": [],
        "total": 2,
        "warnings": [],
    }
    assert "integrate failed at resolve on [[st-2]]: conflict in src/x.py" in _run_end(payload).body.split("\n")


def test_run_end_of_a_cancel_that_escalated_names_the_escalated_card():
    payload = {
        "cancelled": True,
        "run_id": RUN,
        "stopped": [],
        "completed": ["sub-0"],
        "pending": [],
        "escalations": [
            {"level": 0, "story": "st-1", "subtask": "sub-1", "failed_phase": "implement", "detail": "blocked"}
        ],
        "total": 3,
        "warnings": [],
    }
    assert _run_end(payload).body == "\n".join(
        [
            "am · cancelled · run r1",
            "done: 1 of 3",
            "escalated: [[sub-1]] at implement",
            "next: `am run --milestone ms-21f4cf06`",
            _RUN_END_KEY,
        ]
    )


def test_run_end_without_a_total_reports_the_count_alone():
    body = _run_end({"paused": True, "run_id": RUN, "completed": ["sub-0"]}).body
    assert body.split("\n")[1] == "done: 1"


def test_run_end_keys_are_lease_token_scoped():
    first = comments.compose_run_end(run_id=RUN, milestone_id=MILESTONE, token="tok-1", payload={"done": True})
    second = comments.compose_run_end(run_id=RUN, milestone_id=MILESTONE, token="tok-2", payload={"done": True})
    assert first.key == "r1/ms-21f4cf06/run-end:tok-1"
    assert second.key == "r1/ms-21f4cf06/run-end:tok-2"


def test_run_end_escapes_brackets_in_an_integrate_detail():
    payload = {"escalated": True, "phase": "verify", "story": None, "detail": "see [[x]]", "completed": []}
    body = _run_end(payload).body
    assert "integrate failed at verify: see [ [x]]" in body
    assert "[[" not in body


def test_run_end_of_an_unknown_payload_does_not_raise():
    comment = _run_end({})
    assert comment.body == "\n".join(["am · ended · run r1", "done: 0", _RUN_END_KEY])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_comments.py -v`
Expected: the new run-end tests FAIL with `AttributeError: module 'agent_manager.comments' has no attribute 'compose_run_end'`; earlier tests still PASS.

- [ ] **Step 3: Write minimal implementation**

Append to the end of `src/agent_manager/comments.py`:

```python
_RUN_OUTCOMES = ("cancelled", "escalated", "paused", "done")
"""Run outcomes in live-control C6 precedence: the first payload flag set wins."""


def _ref(card_id: str) -> str:
    """A real card id as a brd backlink (B4)."""
    return f"[[{card_id}]]"


def _escalation(payload: Mapping[str, Any]) -> tuple[str, Any] | None:
    """The escalated subtask and its phase: top level, else the first `escalations` row."""
    subtask = payload.get("subtask")
    if subtask and payload.get("failed_phase"):
        return subtask, payload["failed_phase"]
    for row in payload.get("escalations") or []:
        if _field(row, "subtask"):
            return _field(row, "subtask"), _field(row, "failed_phase")
    return None


def _next_command(
    outcome: str,
    *,
    integrate_failed: bool,
    run_id: str,
    milestone_id: str,
    integrated: str | None,
) -> str | None:
    """What a human runs next: relaunch, resume, or merge the integrated branch."""
    if outcome == "cancelled" or integrate_failed:
        return f"am run --milestone {milestone_id}"
    if outcome in ("escalated", "paused"):
        return f"am resume {run_id}"
    if outcome == "done" and integrated:
        return f"git merge {integrated}"
    return None


def compose_run_end(
    *,
    run_id: str,
    milestone_id: str,
    token: str,
    payload: Mapping[str, Any],
) -> Comment:
    """The milestone card's run-end comment, read from `run_milestone`'s payload (B2).

    An Integrate escalation is told apart from a lane one by its `phase` key
    with no `failed_phase`. `total` (the milestone's subtask count) is the
    caller's addition; without it the count stands alone. An unknown payload
    reads as outcome `ended` rather than raising: a comment never fails a run (B8).
    """
    outcome = next((name for name in _RUN_OUTCOMES if payload.get(name)), "ended")
    integrate_failed = (
        outcome == "escalated" and "phase" in payload and "failed_phase" not in payload
    )
    completed = payload.get("completed") or []
    total = payload.get("total")
    lines = [
        f"done: {len(completed)} of {total}" if isinstance(total, int) else f"done: {len(completed)}"
    ]
    escalation = _escalation(payload)
    if escalation is not None:
        card, phase = escalation
        lines.append(f"escalated: {_ref(card)} at {phase}")
    parked = [
        _field(row, "subtask") or _field(row, "story") for row in payload.get("stopped") or []
    ]
    parked = [card for card in parked if card]
    if parked:
        lines.append("parked: " + ", ".join(_ref(card) for card in parked))
    integrated = _field(payload.get("integrated"), "branch")
    if integrated:
        lines.append(f"integrated: {integrated}")
    if integrate_failed:
        story = payload.get("story")
        where = f"{payload['phase']} on {_ref(story)}" if story else str(payload["phase"])
        detail = payload.get("detail")
        line = f"integrate failed at {where}"
        lines.append(f"{line}: {_unlink(str(detail))}" if detail else line)
    following = _next_command(
        outcome,
        integrate_failed=integrate_failed,
        run_id=run_id,
        milestone_id=milestone_id,
        integrated=integrated,
    )
    if following:
        lines.append(f"next: {_cmd(following)}")
    comment_key = key(run_id, milestone_id, f"run-end:{token}")
    body = _render(
        f"am · {outcome} · run {run_id}", lines, f"am-key: {comment_key}", see=f"am status {run_id}"
    )
    return Comment(card_id=milestone_id, key=comment_key, body=body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_comments.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: whole suite PASS (the `e2e` marker stays excluded by `addopts` in `pyproject.toml`); no other test file is affected because no existing module changed.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/comments.py tests/test_comments.py
git commit -m "feat(comments): the milestone run-end comment"
```

---

## Self-Review

**Spec coverage:**
- `CAP`, `Comment`, `key` → Task 1. `agent_reason` (four phases, joined list, all `None` cases, never raises) → Task 1 (spec test 11; Review Focus 4).
- Escalated golden bodies with and without a reason → Task 2 (spec tests 3, 4). 10,000-char truncation → Task 2 (test 8). `[[` escaping → Task 2 (test 9; Review Focus 1, 2). Lease-token-scoped escalated keys → Task 2 (test 10).
- Done fresh/resumed → Task 3 (tests 1, 2), plus the no-agent-text check and the skipped-planning sources. Cancelled → Task 3 (test 5). Base failed on the story → Task 3 (test 6). The `done`/`cancelled`/`base-failed` keys are asserted in their goldens (test 10).
- Run end for done/escalated/paused/cancelled with `[[id]]` refs → Task 4 (test 7). Integrate failure → Task 4. Run-end keys → Task 4 (test 10). Cancel with escalations → Task 4 (Review Focus 5).
- First and last line intact under overflow, including overflow with no agent text → Task 2 (Review Focus 3).
- Out of scope respected: there are no edits to `board.py`, `store.py`, `orchestrate.py`, `bases.py` or `cli.py`, and no enqueue or flush code.

**Placeholder scan:** none. Every code step shows its full code, and every run step gives its command and expected result.

**Type consistency:** `_render(first, before, last, *, see, quote=None, after=())` is used the same way in Tasks 2–4. `_field`, `_unlink` and `_cmd` are defined in Tasks 1–2 before any use. `compose_*` keyword names match the spec interface. `compose_cancelled.before_phase` and the `detail` parameters of `compose_escalated` and `compose_base_failed` also accept `None`, which widens the spec's untyped parameters without narrowing them.
