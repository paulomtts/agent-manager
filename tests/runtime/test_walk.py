"""Rule 1 for the modules that live below pygents (card 7a744199).

`runtime/walk.py` and `runtime/errors.py` sit inside `runtime/` -- the one
package allowed to import pygents -- but `dispatch.py`, `prompt.py` and
`results.py` import them, and none of those may load pygents. Checked in a
fresh interpreter, because this process has pygents loaded already (the
runtime conftest imports it), and a static scan of one file would miss a
transitive import through `runtime/__init__.py`.
"""

import functools
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import eventlines

from agent_manager import models, paths
from agent_manager.store import db as store_db
from agent_manager.store import writer as store_writer
from agent_manager.runtime import walk
from agent_manager.steps import reducers, verify
from agent_manager.runtime.errors import EngineError
from agent_manager.workflow.phases import AgentPhase, Step

PYGENTS_FREE = (
    "agent_manager.runtime",
    "agent_manager.runtime.errors",
    "agent_manager.runtime.walk",
    "agent_manager.dispatch",
    "agent_manager.prompt",
    "agent_manager.results",
    "agent_manager.workflow.phases",
)


@pytest.mark.parametrize("module", PYGENTS_FREE)
def test_importing_the_module_loads_no_pygents(module):
    probe = (
        "import importlib, sys\n"
        f"importlib.import_module({module!r})\n"
        "loaded = sorted(m for m in sys.modules if m == 'pygents' or m.startswith('pygents.'))\n"
        "print(','.join(loaded))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "", f"{module} loaded {done.stdout.strip()}"


# ── gate evaluation (architecture-cleanup S3) ────────────────────────────────
#
# Unit tier per design §14: the evaluator and GateVerdict are pure. The
# run_one_step tests use a real temp Store and a fixed clock -- the same
# lightweight setup tests/test_engine.py's run_one_step tests use -- with no
# git and no harness.

RUN_ID = "run-2026-09-30-01"
STORY_ID = "223f9973"
CARD_ID = "4957ac74"
FIXED = datetime(2026, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_writer.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=CARD_ID,
        branch=f"m13/task-add-gateverdict-and-the-{CARD_ID}",
        base_branch="m13/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _phase_rows(opened) -> list[tuple[str | None, str, str | None]]:
    return [
        (line.phase, line.payload["status"], line.payload["detail"])
        for line in eventlines.run_lines(opened.run_id)
        if line.event == "phase_upsert"
    ]


def _run(store, step: Step, table=None):
    return walk.run_one_step(
        phase=step,
        table={} if table is None else table,
        store=store,
        story_id=STORY_ID,
        subtask=_subtask(),
        clock=lambda: FIXED,
    )


def test_run_one_step_records_a_raising_gate_as_failed_with_the_error_text(store):
    """Spec test 7: walk's own outcome for a broken gate (S3 §6)."""
    later_calls: list[object] = []

    def exploding(result):
        raise ValueError("gate blew up")

    def later(result):
        later_calls.append(result)

    outcome = _run(store, Step("verify", lambda: {"ok": True}, gates=(exploding, later)))

    assert outcome.ok is False
    assert outcome.detail == "ValueError: gate blew up"
    assert later_calls == []
    assert _phase_rows(store) == [
        ("verify", "started", None),
        ("verify", "failed", "ValueError: gate blew up"),
    ]


def test_run_one_step_lets_a_busy_store_error_from_the_step_propagate(store):
    busy = store_db.StoreBusyError("record_phase", 5, 10.0)

    def step() -> dict:
        raise busy

    with pytest.raises(store_db.StoreBusyError) as caught:
        _run(store, Step("verify", step))

    assert caught.value is busy
    assert _phase_rows(store) == [("verify", "started", None)]


def test_run_one_step_records_a_non_mapping_gate_as_failed_engine_error(store):
    """Review Focus 1: the recorded text is today's, byte for byte."""

    def chatty(result):
        return 3

    outcome = _run(store, Step("a", lambda: {}, gates=(chatty,)))

    expected = (
        "EngineError: phase 'a', function 'chatty': gate returned int; a gate "
        "returns None to pass or a mapping verdict to fail, and anything else "
        "would be read as a pass by accident"
    )
    assert outcome.ok is False
    assert outcome.detail == expected
    assert _phase_rows(store)[-1] == ("a", "failed", expected)


def test_run_one_step_keeps_earlier_warnings_when_a_later_gate_breaks(store):
    """Review Focus 2: warnings gathered before the break stay on the outcome."""

    def first(result):
        return {"warn": "one"}

    def second(result):
        return {"warn": "two"}

    def exploding(result):
        raise OSError("disk went away")

    outcome = _run(store, Step("a", lambda: {}, gates=(first, second, exploding)))

    assert outcome.ok is False
    assert outcome.detail == "OSError: disk went away"
    assert outcome.warnings == [
        "phase 'a' gate 'first' warned: one",
        "phase 'a' gate 'second' warned: two",
    ]


def _step(*gates) -> Step:
    return Step("verify", lambda: {}, gates=tuple(gates))


def test_gate_values_is_public_and_the_private_name_still_resolves():
    context = {"card": "c1", "worktree": "/w"}

    values = walk.gate_values(context, "build", {"x": 1})

    assert values == {"card": "c1", "worktree": "/w", "result": {"x": 1}, "build": {"x": 1}}
    assert walk._gate_values is walk.gate_values


def test_evaluate_gates_passes_when_every_gate_returns_none():
    """Spec test 1."""
    calls: list[str] = []

    def first(result):
        calls.append("first")

    def second(card):
        calls.append("second")

    warnings: list[str] = []

    verdict = walk.evaluate_gates(_step(first, second), {"result": {}, "card": "c1"}, warnings)

    assert verdict == walk.GateVerdict("pass", None)
    assert verdict.detail is None
    assert calls == ["first", "second"]
    assert warnings == []


def test_evaluate_gates_warns_and_keeps_going_after_a_warning_gate():
    """Spec test 2."""
    calls: list[str] = []

    def cautious(result):
        return {"warn": "coverage dipped"}

    def fine(result):
        calls.append("fine")

    warnings = ["earlier"]

    verdict = walk.evaluate_gates(_step(cautious, fine), {"result": {}}, warnings)

    message = "phase 'verify' gate 'cautious' warned: coverage dipped"
    assert verdict.kind == "warn"
    assert verdict.detail == {"warnings": [message]}
    assert warnings == ["earlier", message]
    assert calls == ["fine"]


def test_evaluate_gates_fails_at_the_first_failing_mapping_and_stops():
    """Spec test 3, driven through an AgentPhase to show both phase kinds work."""
    later_calls: list[str] = []

    def blocking(result):
        return {"reason": "red", "count": 2}

    def later(result):
        later_calls.append("later")

    phase = AgentPhase("review", "critic", (), None, gates=(blocking, later))

    verdict = walk.evaluate_gates(phase, {"result": {}}, [])

    assert verdict.kind == "fail"
    assert verdict.detail == {
        "gate": "blocking",
        "verdict": {"reason": "red", "count": 2},
        "message": "phase 'review' gate 'blocking' failed: count=2, reason=red",
    }
    assert later_calls == []


def test_evaluate_gates_reports_a_raising_gate_as_broken():
    """Spec test 4: the one "raises" test against the shared evaluator (S3 §6)."""
    boom = ValueError("gate blew up")
    later_calls: list[str] = []

    def exploding(result):
        raise boom

    def later(result):
        later_calls.append("later")

    verdict = walk.evaluate_gates(_step(exploding, later), {"result": {}}, [])

    assert verdict.kind == "broken"
    assert verdict.detail == {"gate": "exploding", "reason": "raised", "error": boom}
    assert verdict.detail["error"] is boom
    assert later_calls == []


def test_evaluate_gates_reports_a_non_mapping_gate_as_broken():
    """Spec test 5."""

    def chatty(result):
        return 3

    verdict = walk.evaluate_gates(_step(chatty), {"result": {}}, [])

    assert verdict.kind == "broken"
    assert set(verdict.detail) == {"gate", "reason", "error", "returned_type"}
    assert verdict.detail["gate"] == "chatty"
    assert verdict.detail["reason"] == "not_a_mapping"
    assert verdict.detail["returned_type"] == "int"
    error = verdict.detail["error"]
    assert isinstance(error, EngineError)
    assert error.phase == "verify"
    assert error.function == "chatty"
    assert str(error) == (
        "phase 'verify', function 'chatty': gate returned int; a gate returns "
        "None to pass or a mapping verdict to fail, and anything else would be "
        "read as a pass by accident"
    )


def test_evaluate_gates_lets_a_binding_failure_propagate():
    """Spec test 6: a binding failure is not a verdict."""

    def needs(missing):
        return None

    with pytest.raises(EngineError) as info:
        walk.evaluate_gates(_step(needs), {"result": {}}, [])

    assert info.value.phase == "verify"
    assert info.value.function == "needs"
    assert info.value.parameter == "missing"


def test_evaluate_gates_fails_an_empty_mapping():
    """Review Focus 3: `{}` is a failing verdict today, not a pass."""

    def empty(result):
        return {}

    verdict = walk.evaluate_gates(_step(empty), {"result": {}}, [])

    assert verdict.kind == "fail"
    assert verdict.detail["message"] == "phase 'verify' gate 'empty' failed: "


def test_evaluate_gates_lets_warn_win_over_other_keys():
    """Review Focus 4: a mapping holding `warn` warns, whatever else it holds."""

    def mixed(result):
        return {"warn": "soft", "blocked": "x"}

    warnings: list[str] = []

    verdict = walk.evaluate_gates(_step(mixed), {"result": {}}, warnings)

    assert verdict.kind == "warn"
    assert warnings == ["phase 'verify' gate 'mixed' warned: soft"]


def test_evaluate_gates_does_not_swallow_a_base_exception():
    """Review Focus 5: control-flow signals are not `broken`."""

    class _Signal(BaseException):
        pass

    def signalling(result):
        raise _Signal()

    with pytest.raises(_Signal):
        walk.evaluate_gates(_step(signalling), {"result": {}}, [])


def test_run_one_step_records_a_fail_verdict_with_its_message(store, monkeypatch):
    """run_one_step takes its gate outcome from evaluate_gates, not a private copy."""
    monkeypatch.setattr(
        walk,
        "evaluate_gates",
        lambda phase, values, warnings: walk.GateVerdict(
            "fail", {"gate": "g", "verdict": {"k": "v"}, "message": "from the verdict"}
        ),
    )

    outcome = _run(store, Step("a", lambda: {}))

    assert outcome.ok is False
    assert outcome.detail == "from the verdict"
    assert _phase_rows(store)[-1] == ("a", "failed", "from the verdict")


def test_run_one_step_sends_a_broken_verdict_through_the_catch_all(store, monkeypatch):
    monkeypatch.setattr(
        walk,
        "evaluate_gates",
        lambda phase, values, warnings: walk.GateVerdict(
            "broken", {"gate": "g", "reason": "raised", "error": KeyError("gone")}
        ),
    )

    outcome = _run(store, Step("a", lambda: {}))

    assert outcome.ok is False
    assert outcome.detail == "KeyError: 'gone'"
    assert _phase_rows(store)[-1] == ("a", "failed", "KeyError: 'gone'")


# ── moved from tests/test_dispatch.py when dispatch's copy was deleted (S3) ──


def test_a_reserved_key_is_not_overwritten_by_a_same_named_phase():
    values = walk.gate_values({"worktree": Path("/repo/wt")}, "worktree", {"created": True})

    assert values["worktree"] == Path("/repo/wt")
    assert values["result"] == {"created": True}
    assert "worktree" in walk.RESERVED_CONTEXT_KEYS


def _blocking_gate(result, blocked):
    return {"blocked": blocked}


def test_a_callable_gate_without_a_name_is_named_by_its_repr():
    # A functools.partial has no __name__; the display name falls back to repr.
    gate = functools.partial(_blocking_gate, blocked="x")
    name = repr(gate)
    phase = AgentPhase("explore", "explorer", (), None, gates=(gate,))

    verdict = walk.evaluate_gates(
        phase, walk.gate_values({}, "explore", {"summary": "ok"}), []
    )

    assert verdict.kind == "fail"
    assert verdict.detail["message"] == f"phase 'explore' gate {name!r} failed: blocked=x"


# ── the engine-supplied log directory (spec e1b1e7d5, Decision 1) ────────────
#
# Unit tier: fake step functions and a real temp Store (file I/O only), like
# the run_one_step tests above. The one test that runs the real verify step
# spawns a real interpreter and is marked `git` explicitly.


def _attempt(n: int, phase: str = "verify") -> Path:
    """Where the engine puts attempt `n`; computed without creating it."""
    return paths.run_dir(RUN_ID) / CARD_ID / f"{phase}.{n}"


def _logging_step(seen: list[object]):
    def logging_step(log_dir=None):
        seen.append(log_dir)
        return {}

    return logging_step


def test_run_one_step_hands_a_declaring_step_a_fresh_attempt_dir_each_call(store):
    seen: list[object] = []
    step = Step("verify", _logging_step(seen))

    first = _run(store, step)
    second = _run(store, step)

    assert first.ok is True and second.ok is True
    assert seen == [_attempt(1), _attempt(2)]
    assert _attempt(1).is_dir()
    assert _attempt(2).is_dir()


def test_run_one_step_creates_no_attempt_dir_for_a_step_that_does_not_declare_log_dir(
    store,
):
    outcome = _run(store, Step("verify", lambda: {}))

    assert outcome.ok is True
    assert not _attempt(1).exists()


def test_the_engines_log_dir_overrides_document_args_and_the_binding_table(store):
    seen: list[object] = []
    step = Step("verify", _logging_step(seen), args={"log_dir": "/elsewhere"})

    _run(store, step, table={"log_dir": "/from-the-table"})

    assert seen == [_attempt(1)]


def test_run_one_step_never_overwrites_an_earlier_attempt_directory(store):
    # Review Focus 3: a re-run after resume finds `verify.1` from the previous
    # process on disk and lands in `verify.2`.
    earlier = paths.attempt_dir(RUN_ID, CARD_ID, "verify", 1)
    (earlier / "stdout.log").write_text("the first run's evidence\n", encoding="utf-8")
    seen: list[object] = []

    _run(store, Step("verify", _logging_step(seen)))

    assert seen == [_attempt(2)]
    assert (earlier / "stdout.log").read_text(encoding="utf-8") == (
        "the first run's evidence\n"
    )


class _RunlessStore:
    """A store with no run id: only `record_phase`, which the walk calls."""

    def record_phase(self, story_id, card_id, phase) -> None:
        pass


def test_a_store_with_no_run_id_leaves_the_steps_default(tmp_path):
    seen: list[object] = []

    outcome = walk.run_one_step(
        phase=Step("verify", _logging_step(seen), args={"log_dir": "/elsewhere"}),
        table={},
        store=_RunlessStore(),
        story_id=STORY_ID,
        subtask=_subtask(),
        clock=lambda: FIXED,
    )

    assert outcome.ok is True
    assert seen == [None]


@pytest.mark.git
def test_a_red_verify_through_run_one_step_leaves_its_logs_under_the_run_dir(
    store, tmp_path
):
    """Card test: production wiring with the real step and the real gate."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    script = (
        "import sys; print('7 passed'); sys.stderr.write('E boom\\n'); "
        "raise SystemExit(1)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    step = Step("verify", verify.run_suite, gates=(reducers.verification_passed_gate,))

    outcome = _run(
        store, step, table={"commands": [command], "worktree": str(worktree)}
    )

    assert outcome.ok is False
    assert _phase_rows(store)[-1][:2] == ("verify", "failed")
    log_dir = _attempt(1)
    assert (log_dir / verify.STDOUT_LOG).read_text(encoding="utf-8") == (
        f"==> {command} (exit 1)\n7 passed\n"
    )
    assert (log_dir / verify.STDERR_LOG).read_text(encoding="utf-8") == (
        f"==> {command} (exit 1)\nE boom\n"
    )


# ── the engine-supplied run id (spec e2efd21d, B3) ───────────────────────────
#
# Unit tier: fake steps (and the real verify step with a fake runner via the
# document's `args`), a real temp Store, no spawn.


def _run_id_step(seen: list[object]):
    def run_id_step(run_id=None):
        seen.append(run_id)
        return {}

    return run_id_step


def test_run_one_step_hands_a_declaring_step_the_stores_run_id(store):
    # T9
    seen: list[object] = []

    outcome = _run(store, Step("verify", _run_id_step(seen)))

    assert outcome.ok is True
    assert seen == [RUN_ID]


def test_the_engines_run_id_overrides_document_args_and_the_binding_table(store):
    # T10 / Review Focus 5: `bind_arguments` accepts the args key, since the
    # step declares it, and the engine's value still wins.
    seen: list[object] = []
    step = Step("verify", _run_id_step(seen), args={"run_id": "other"})

    outcome = _run(store, step, table={"run_id": "from-the-table"})

    assert outcome.ok is True
    assert seen == [RUN_ID]


def test_a_store_with_no_run_id_leaves_the_steps_run_id_default():
    # T11
    seen: list[object] = []

    outcome = walk.run_one_step(
        phase=Step("verify", _run_id_step(seen), args={"run_id": "other"}),
        table={"run_id": "from-the-table"},
        store=_RunlessStore(),
        story_id=STORY_ID,
        subtask=_subtask(),
        clock=lambda: FIXED,
    )

    assert outcome.ok is True
    assert seen == [None]


def test_a_step_that_does_not_declare_run_id_is_called_without_it(store):
    # T12
    seen: list[dict[str, object]] = []

    def step(**kwargs):
        seen.append(kwargs)
        return {}

    def plain(card):
        seen.append({"card": card})
        return {}

    first = _run(store, Step("verify", step), table={"run_id": "from-the-table"})
    second = _run(
        store, Step("verify", plain), table={"run_id": "x", "card": CARD_ID}
    )

    assert first.ok is True and second.ok is True
    assert seen == [{}, {"card": CARD_ID}]


def test_the_real_verify_step_hands_its_runner_the_walks_run_and_card_ids(
    store, tmp_path
):
    """Card test (T13): a fake runner sees both variables with the walk's ids."""
    calls: list[tuple[list[str], str, object]] = []

    def runner(argv, cwd, env=None):
        calls.append((argv, cwd, env))
        return verify.CommandResult(exit_code=0, stdout="ok\n", stderr="")

    worktree = tmp_path / "wt"
    worktree.mkdir()
    step = Step(
        "verify",
        verify.run_suite,
        args={"runner": runner},
        gates=(reducers.verification_passed_gate,),
    )

    outcome = _run(
        store,
        step,
        table={
            "commands": ["uv run pytest"],
            "worktree": str(worktree),
            "card": CARD_ID,
        },
    )

    assert outcome.ok is True
    assert set(outcome.result) == {"passed", "verified", "detail"}
    assert calls == [
        (
            ["uv", "run", "pytest"],
            str(worktree),
            {"AM_RUN_ID": RUN_ID, "AM_CARD_ID": CARD_ID},
        )
    ]
