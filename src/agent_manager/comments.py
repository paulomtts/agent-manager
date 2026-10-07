"""Outcome comments for brd cards (board-comments design B2-B9).

`key`, `agent_reason` and every `compose_*` are pure: no I/O, no `brd`, no
store, no clock. Each `compose_*` turns one outcome's data into a `Comment`.
Agent text enters only through `agent_reason`'s three failure fields, quoted,
`[[`-escaped and cut first when a body would exceed `CAP`.

`enqueue` and `flush` are the outbox side (B6-B9). `enqueue` queues a
`Comment` in the store's `board_comments` table. `flush` posts the pending
rows through an injected `board_api` (the `board` module by default), one
row at a time under the board write lock and never inside a store
transaction, checking the card for the row's `am-key:` line first so a crash
between posting and marking never double-posts. A board failure becomes a
returned warning, never an exception.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_manager import board, locks, models
from agent_manager.store import COMMENT_ATTEMPTS

if TYPE_CHECKING:
    from agent_manager.runtime.walk import SubtaskSummary
    from agent_manager.store import CommentRow, Store

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


def compose_canceled(
    *,
    run_id: str,
    card_id: str,
    before_phase: str | None,
    branch: str,
    relaunch: str,
) -> Comment:
    """A subtask a cancel left `in_progress`: where it stopped, its branch, how to relaunch.

    Its key keeps the legacy `cancelled` spelling, so a cancel comment an
    older `am` queued or posted is the same comment and is never posted twice.
    """
    lines: list[str] = []
    if before_phase is not None:
        lines.append(f"stopped before: {before_phase}")
    lines.append(f"branch: {branch}")
    lines.append(f"relaunch: {_cmd(relaunch)}")
    comment_key = key(run_id, card_id, "cancelled")
    body = _render(
        f"am · {models.CANCELED} · run {run_id}",
        lines,
        f"am-key: {comment_key}",
        see=f"am status {run_id}",
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


_RUN_OUTCOMES = ("escalated", "paused", "done")
"""Run outcomes below a cancel, in precedence order: the first payload flag set wins."""


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
    if outcome == models.CANCELED or integrate_failed:
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
    """The milestone card's run-end comment, read from `run_milestone`'s payload.

    A cancel, set under either of its keys (`canceled`, `cancelled`), wins
    over every other outcome and reads as `canceled`; then `escalated`,
    `paused`, `done`. An Integrate escalation is told apart from a lane one by
    its `phase` key with no `failed_phase`. `total` (the milestone's subtask
    count) is the caller's addition; without it the count stands alone. An
    unknown payload reads as outcome `ended` rather than raising: a comment
    never fails a run.
    """
    if any(payload.get(name) for name in models.CANCELED_STATUSES):
        outcome = models.CANCELED
    else:
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


# -- outbox (board-comments B6-B9) ---------------------------------------------


def enqueue(store: Store, comment: Comment, *, run_id: str, now: datetime) -> None:
    """Queue `comment` in the store's outbox under its key, once (B6, B9).

    A pass-through to `Store.enqueue_comment`: a key already queued, in any
    state, is left exactly as it was. Nothing is posted here; the caller
    flushes. Nothing is caught: a lost lease raises `store_leases.LeaseLostError`
    and writes no row.
    """
    store.enqueue_comment(
        run_id=run_id,
        card_id=comment.card_id,
        key=comment.key,
        body=comment.body,
        now=now,
    )


def _post_one(store: Store, row: CommentRow, root: Path, board_api: Any) -> None:
    """Put `row` on its card unless its `am-key:` line is already there, then mark it.

    Called with the board write lock held. A comment whose body (trailing
    whitespace ignored) ends with `am-key: <key>` is this row, posted by an
    earlier flush that died before marking (B7); its id is recorded instead
    of posting again.
    """
    marker = f"am-key: {row.key}"
    existing = board_api.comment_list(row.card_id, repo_dir=root)
    match = next((c for c in existing if c.body.rstrip().endswith(marker)), None)
    if match is None:
        comment_id = board_api.comment_add(row.card_id, row.body, author="am", repo_dir=root)
    else:
        comment_id = match.id
    store.mark_comment_posted(row.key, comment_id, datetime.now(timezone.utc))


def _warning(row: CommentRow, attempts: int, error: Exception) -> str:
    """The one report warning for a row whose post failed (B7, B8).

    `attempts` is `Store.record_comment_failure`'s new count; at
    `COMMENT_ATTEMPTS` the store has already marked the row `abandoned`.
    """
    where = f"board comment {row.key} on card {row.card_id}"
    if attempts >= COMMENT_ATTEMPTS:
        return f"{where} abandoned after {attempts} failed attempts: {error}"
    return f"{where} not posted (attempt {attempts} of {COMMENT_ATTEMPTS}), will retry: {error}"


def flush(
    store: Store,
    root: Path,
    *,
    run_id: str | None = None,
    card_ids: Iterable[str] | None = None,
    board_api: Any = board,
) -> list[str]:
    """Post every pending outbox row, oldest first, and return the warnings (B7).

    `run_id`/`card_ids` narrow the rows exactly as `Store.pending_comments`
    does. Each row takes `board_api.write_lock(root)` on its own -- never
    while a store transaction is open (X8) -- and all board access goes
    through `board_api`, so tests inject a fake.

    A `board.BoardError` or a `locks.LockTimeoutError` on a row is counted
    with `Store.record_comment_failure`, reported as exactly one warning,
    and the next row is tried: a board failure never escalates, parks or
    changes a run (B8). Every other exception propagates.
    """
    warnings: list[str] = []
    for row in store.pending_comments(run_id, card_ids):
        try:
            with board_api.write_lock(root):
                _post_one(store, row, root, board_api)
        except (board.BoardError, locks.LockTimeoutError) as error:
            # Counted outside the board lock; the store's own 3-strike rule
            # abandons the row. Anything else (a lost lease, a crash) propagates.
            attempts = store.record_comment_failure(row.key)
            warnings.append(_warning(row, attempts, error))
    return warnings
