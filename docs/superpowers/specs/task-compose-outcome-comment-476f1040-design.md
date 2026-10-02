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
