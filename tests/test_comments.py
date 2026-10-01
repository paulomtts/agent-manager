"""Outcome comments (board-comments design B2-B9): pure `compose_*` unit tests,
then the outbox (`enqueue`/`flush`) against a real temporary store and a fake
`board_api` -- no `brd` process, no network."""

import contextlib
import dataclasses
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_manager import board, comments, store
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


def test_agent_reason_skips_empty_review_blockers():
    results = {"review": {"unresolved_blockers": ["", "missing test", ""]}}
    assert comments.agent_reason(results, "review") == "missing test"


def test_escalated_detail_brackets_are_broken():
    comment = _escalated(reason=None, detail="gate saw [[4f31e025-aaaa]]")
    assert "detail: gate saw [ [4f31e025-aaaa]]" in comment.body.split("\n")
    assert "[[" not in comment.body


def test_an_over_cap_detail_drops_the_reason_and_still_fits():
    comment = _escalated(reason="short reason", detail="d" * 5000, phase="implement")
    lines = comment.body.split("\n")
    assert len(comment.body) <= comments.CAP
    assert "short reason" not in comment.body
    assert lines[0] == "am · escalated · run r1"
    assert lines[1].startswith("phase: implement")
    assert lines[-4] == "… (truncated; see `am logs r1 card-476f1040 --phase implement`)"
    assert lines[-3] == "next: `am resume r1`"
    assert lines[-2] == "why: `am logs r1 card-476f1040 --phase implement`"
    assert lines[-1] == "am-key: r1/card-476f1040/escalated:tok-1"


def test_done_lists_only_the_verify_commands_that_passed():
    summary = _done_summary()
    summary.results["verify"] = {
        "passed": False,
        "verified": [
            {"command": "uv run pytest", "ok": True, "tail": ""},
            {"command": "uv run ruff check", "ok": False, "tail": ""},
        ],
        "detail": "",
    }
    lines = _done(summary=summary).body.split("\n")
    assert "verified: `uv run pytest`" in lines
    assert "ruff" not in "\n".join(lines)


def test_cancelled_without_a_phase_omits_the_stopped_before_line():
    comment = comments.compose_cancelled(
        run_id=RUN,
        card_id=CARD,
        before_phase=None,
        branch="m12/task-x-476f1040",
        relaunch="am run --milestone ms-21f4cf06",
    )
    assert comment.body == "\n".join(
        [
            "am · cancelled · run r1",
            "branch: m12/task-x-476f1040",
            "relaunch: `am run --milestone ms-21f4cf06`",
            "am-key: r1/card-476f1040/cancelled",
        ]
    )


def test_base_failed_detail_brackets_are_broken_and_an_over_cap_detail_is_cut():
    comment = comments.compose_base_failed(
        run_id=RUN,
        story_id=STORY,
        base_branch="m12/base-story-60189137",
        detail="[[x]] " * 1000,
    )
    lines = comment.body.split("\n")
    assert len(comment.body) <= comments.CAP
    assert "[[" not in comment.body
    assert lines[0] == "am · base failed · run r1"
    assert lines[-2] == "… (truncated; see `am status r1`)"
    assert lines[-1] == "am-key: r1/story-60189137/base-failed"


# -- outbox: enqueue and flush (board-comments B6-B9) ---------------------------
#
# Unit tier: a real temporary `Store` (XDG_DATA_HOME is redirected by
# tests/conftest.py) and a fake `board_api`. The real-brd proof is the e2e
# sibling's job.


@pytest.fixture
def root(tmp_path) -> Path:
    """A stand-in for the project worktree the store and board are keyed by."""
    project = tmp_path / "repo"
    project.mkdir()
    return project


@pytest.fixture
def stores(root) -> Iterator[Callable[..., store.Store]]:
    """Open any number of `Store`s on `root`, each on its own connection; close them all.

    A local copy of `tests/test_store.py`'s `stores` fixture (M10 takeover pattern).
    """
    opened: list[store.Store] = []

    def open_store(run_id: str = RUN) -> store.Store:
        st = store.Store.open(root, run_id)
        opened.append(st)
        return st

    yield open_store
    for st in opened:
        st.close()


def _at(minute: int) -> datetime:
    return datetime(2026, 9, 30, 12, minute, tzinfo=timezone.utc)


def _alive(row: store.LeaseRow) -> bool:
    return True


def _dead(row: store.LeaseRow) -> bool:
    return False


def _comment(card_id: str, event: str, *, run_id: str = RUN) -> comments.Comment:
    comment_key = comments.key(run_id, card_id, event)
    body = f"am · {event} · run {run_id}\nbranch: m12/x — é `cmd`\nam-key: {comment_key}"
    return comments.Comment(card_id=card_id, key=comment_key, body=body)


def _row(st: store.Store, key: str):
    return st.connection.execute(
        "SELECT * FROM board_comments WHERE key = ?", (key,)
    ).fetchone()


def test_enqueue_queues_one_pending_row_per_key(stores):
    st = stores()
    first = _comment("card-a", "done")

    assert comments.enqueue(st, first, run_id=RUN, now=_at(0)) is None
    comments.enqueue(
        st, dataclasses.replace(first, body="a different body"), run_id=RUN, now=_at(1)
    )

    rows = st.pending_comments()
    assert [(r.run_id, r.card_id, r.key, r.body, r.state) for r in rows] == [
        (RUN, "card-a", first.key, first.body, "pending")
    ]
    count = st.connection.execute("SELECT COUNT(*) FROM board_comments").fetchone()[0]
    assert count == 1


def test_enqueue_on_a_taken_over_store_raises_and_writes_no_row(stores):
    a = stores()
    a.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    b = stores()
    b.take_lease(token="t2", pid=2, host="h", now=_at(1), is_live=_dead)
    comment = _comment("card-a", "done")

    with pytest.raises(store.LeaseLostError) as caught:
        comments.enqueue(a, comment, run_id=RUN, now=_at(2))

    assert caught.value.holder is not None and caught.value.holder.token == "t2"
    assert a.connection.in_transaction is False
    assert _row(b, comment.key) is None


class FakeBoard:
    """A stand-in for the `board` module: per-card comments, a call log, a tracked lock.

    `down` makes every board call raise; `fail_cards` makes calls for those
    cards raise; `lock_timeout` makes `write_lock` raise on entry, as a real
    `ProcessLock` does. Every failure is the real `board.BoardError` /
    `locks.LockTimeoutError`.
    """

    def __init__(self) -> None:
        self.cards: dict[str, list[board.BoardComment]] = {}
        self.calls: list[tuple[str, str, int, Path | None]] = []
        self.added: list[tuple[str, str, str]] = []
        self.held = 0
        self.locks_taken = 0
        self.down = False
        self.fail_cards: set[str] = set()
        self.lock_timeout = False
        self._next_id = 0

    @contextlib.contextmanager
    def write_lock(self, repo_dir):
        if self.lock_timeout:
            from agent_manager import locks

            raise locks.LockTimeoutError(Path(repo_dir) / "board.lock", 0.0)
        self.locks_taken += 1
        self.held += 1
        try:
            yield
        finally:
            self.held -= 1

    def _call(self, op: str, card_id: str, repo_dir) -> None:
        self.calls.append((op, card_id, self.held, repo_dir))
        if self.down or card_id in self.fail_cards:
            raise board.BoardError(
                f"brd comment {op} failed", argv=["brd", "comment", op, card_id], exit_code=1
            )

    def comment_list(self, card_id, *, repo_dir=None):
        self._call("list", card_id, repo_dir)
        return list(self.cards.get(card_id, []))

    def comment_add(self, card_id, body, *, author="am", repo_dir=None):
        self._call("add", card_id, repo_dir)
        self._next_id += 1
        comment = board.BoardComment(id=f"c{self._next_id}", body=body, author=author)
        self.cards.setdefault(card_id, []).append(comment)
        self.added.append((card_id, body, author))
        return comment.id


class _Crash(Exception):
    """The process dying between `comment_add` and marking the row posted."""


class CrashOnFirstMark:
    """A `Store` whose first `mark_comment_posted` raises `error`; everything else delegates."""

    def __init__(self, inner: store.Store, error: BaseException) -> None:
        self._inner = inner
        self._error = error
        self.marks = 0

    def mark_comment_posted(self, key, comment_id, now):
        self.marks += 1
        if self.marks == 1:
            raise self._error
        self._inner.mark_comment_posted(key, comment_id, now)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _queue(st, card_id: str, event: str, *, minute: int = 0, run_id: str = RUN):
    comment = _comment(card_id, event, run_id=run_id)
    comments.enqueue(st, comment, run_id=run_id, now=_at(minute))
    return comment


def test_flush_posts_pending_rows_oldest_first_as_am(stores, root):
    st = stores()
    newest = _queue(st, "card-a", "done", minute=2)
    oldest = _queue(st, "card-b", "done", minute=0)
    middle = _queue(st, "card-c", "done", minute=1)
    fake = FakeBoard()

    warnings = comments.flush(st, root, board_api=fake)

    assert warnings == []
    assert fake.added == [
        ("card-b", oldest.body, "am"),
        ("card-c", middle.body, "am"),
        ("card-a", newest.body, "am"),
    ]
    assert st.pending_comments() == []
    posted = {c.key: _row(st, c.key) for c in (oldest, middle, newest)}
    assert [(posted[c.key]["state"], posted[c.key]["comment_id"]) for c in (oldest, middle, newest)] == [
        ("posted", "c1"),
        ("posted", "c2"),
        ("posted", "c3"),
    ]
    assert all(posted[c.key]["posted_at"] is not None for c in (oldest, middle, newest))


def test_flush_calls_the_board_only_under_its_write_lock_once_per_row(stores, root):
    st = stores()
    _queue(st, "card-a", "done", minute=0)
    _queue(st, "card-b", "done", minute=1)
    fake = FakeBoard()

    comments.flush(st, root, board_api=fake)

    assert fake.calls == [
        ("list", "card-a", 1, root),
        ("add", "card-a", 1, root),
        ("list", "card-b", 1, root),
        ("add", "card-b", 1, root),
    ]
    assert fake.locks_taken == 2
    assert fake.held == 0


def test_flush_filters_by_run_and_cards(stores, root):
    st = stores()
    a = _queue(st, "card-a", "done", minute=0, run_id="r1")
    b = _queue(st, "card-b", "done", minute=1, run_id="r2")
    c = _queue(st, "card-c", "done", minute=2, run_id="r1")
    fake = FakeBoard()

    assert comments.flush(st, root, run_id="r2", board_api=fake) == []
    assert [added[0] for added in fake.added] == ["card-b"]

    assert comments.flush(st, root, card_ids=["card-c"], board_api=fake) == []
    assert [added[0] for added in fake.added] == ["card-b", "card-c"]

    assert comments.flush(st, root, card_ids=[], board_api=fake) == []
    assert [added[0] for added in fake.added] == ["card-b", "card-c"]

    assert [r.key for r in st.pending_comments()] == [a.key]
    assert _row(st, b.key)["state"] == "posted"
    assert _row(st, c.key)["state"] == "posted"


def test_flush_with_nothing_pending_never_touches_the_board(stores, root):
    st = stores()
    fake = FakeBoard()

    assert comments.flush(st, root, board_api=fake) == []
    assert fake.calls == []
    assert fake.locks_taken == 0


def test_a_crash_between_post_and_mark_never_double_posts(stores, root):
    st = stores()
    comment = _queue(st, "card-a", "done")
    fake = FakeBoard()
    crashing = CrashOnFirstMark(st, _Crash("killed before marking"))

    with pytest.raises(_Crash):
        comments.flush(crashing, root, board_api=fake)

    # The comment reached the board, the row did not move, and the lock is free.
    assert [c.body for c in fake.cards["card-a"]] == [comment.body]
    assert [r.key for r in st.pending_comments()] == [comment.key]
    assert _row(st, comment.key)["failed_attempts"] == 0
    assert fake.held == 0

    assert comments.flush(crashing, root, board_api=fake) == []

    assert fake.added == [("card-a", comment.body, "am")]
    assert len(fake.cards["card-a"]) == 1
    row = _row(st, comment.key)
    assert (row["state"], row["comment_id"]) == ("posted", "c1")


def test_flush_recognises_a_posted_body_with_trailing_whitespace(stores, root):
    st = stores()
    comment = _queue(st, "card-a", "done")
    fake = FakeBoard()
    fake.cards["card-a"] = [
        board.BoardComment(id="c-prior", body=comment.body + "\n\n  ", author="am")
    ]

    assert comments.flush(st, root, board_api=fake) == []

    assert fake.added == []
    row = _row(st, comment.key)
    assert (row["state"], row["comment_id"]) == ("posted", "c-prior")


def test_flush_ignores_comments_whose_last_line_is_another_key(stores, root):
    st = stores()
    earlier = comments.key(RUN, "card-a", "escalated:tok-1")
    comment = _queue(st, "card-a", "escalated:tok-2")
    fake = FakeBoard()
    fake.cards["card-a"] = [
        board.BoardComment(
            id="c-old", body=f"am · escalated · run {RUN}\nam-key: {earlier}", author="am"
        ),
        board.BoardComment(
            id="c-human", body=f"see am-key: {comment.key}\nthanks", author="paulo"
        ),
    ]

    assert comments.flush(st, root, board_api=fake) == []

    assert fake.added == [("card-a", comment.body, "am")]
    assert _row(st, comment.key)["comment_id"] == "c1"
