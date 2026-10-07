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

import json
import math
import re
from collections.abc import Mapping


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


# These gates were ported from task.js and read the camelCase keys the JS
# harness wrote. `results.py`'s models are snake_case, and `dispatch.py` dumps
# them without `by_alias=True`, so BOTH spellings genuinely reach a gate: a
# validated model dump is snake_case, and a hand-written result file (or the
# ported fixtures in `tests/steps/test_reducers.py`) is camelCase. Reading both
# is a compatibility shim on the read, not a second rule -- no verdict text,
# threshold or ordering below depends on which spelling arrived. Doing it here,
# in one helper, is what keeps the alternative from happening: two registry
# wrappers that would quietly become a second place gate semantics live.
def _either_field(mapping: object, snake: str, camel: str) -> object:
    """``snake``'s value if it has one, else ``camel``'s, else ``None``."""
    value = _field(mapping, snake)
    return _field(mapping, camel) if value is None else value


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
    raw_commit = _either_field(review, "commit_count", "commitCount")
    raw_tagged = _either_field(review, "tagged_count", "taggedCount")
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


# A Plan-Hash is the first 8 hex characters of sha256sum(<plan file>).
# fullmatch, not match/search with `$`: Python's `$` also matches just before a
# final newline, so `"a1b2c3d4\n"` would slip through a `^...$` pattern.
_PLAN_HASH = re.compile(r"[0-9a-f]{8}")


def is_plan_hash(value: object) -> bool:
    """``True`` only for a ``str`` of exactly 8 lowercase hex characters."""
    return isinstance(value, str) and _PLAN_HASH.fullmatch(value) is not None


# The hash `docs_commit` recorded is the single source of truth: Implement
# stamps it, Review is handed it and counts trailers against it. The live plan
# file is NOT re-hashed, because an agent may append to it after the trailers
# are written (the file is often git-ignored), which would make a recomputed
# hash disagree with every correct commit. A reviewer that still reports a
# different hash is wrong about its own report, not about the branch, so this
# is a warning and never a stop.
def plan_hash_mismatch(recorded: object, reported: object) -> str | None:
    """The warning text, or ``None`` when there is nothing trustworthy to say."""
    if not is_plan_hash(recorded) or not is_plan_hash(reported):
        return None
    if recorded == reported:
        return None
    return (
        f"review reported plan_hash {reported}, but docs_commit recorded "
        f"{recorded}; the recorded value is authoritative and the report is "
        "ignored (the plan file may have changed after its trailers were written)."
    )


def plan_hash_gate(recorded: object, reported: object) -> dict[str, str] | None:
    """``{"warn": ...}`` when the reported hash differs from the recorded one, else ``None``.

    Never blocks: the stop on missing trailers is :func:`review_gate`'s.
    """
    detail = plan_hash_mismatch(recorded, reported)
    return None if detail is None else {"warn": detail}


def stale_branch_gate(result: object) -> dict[str, str] | None:
    """``{"warn": ...}`` naming the preserved ref when `docs_commit` retired a stale branch."""
    preserved = _field(result, "preserved_ref")
    if not preserved:
        return None
    return {
        "warn": f"branch held only commits of an older plan; preserved at {preserved} "
        "and recreated from the base"
    }


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

    full_suite = _either_field(
        _field(explore, "verification"), "full_suite", "fullSuite"
    )
    if not isinstance(full_suite, list):
        return {"detail": "exploration did not return an array for verification.fullSuite"}

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

    if any(not isinstance(cmd, str) or len(cmd.strip()) < 3 for cmd in full_suite):
        return {
            "detail": "exploration's verification.fullSuite contains an implausible "
            f"command: {_json(full_suite)}"
        }
    return None


# The `verify` phase's gate (`TASK`). `verify.run_suite` reports
# what happened and judges nothing -- it returns `passed: false` for a red
# command and raises `VerifyError` only for a command it could not launch at
# all. Something has to turn "red" into a stop, and doing it here rather than
# inside the step keeps the one rule in one place, the way `review_gate` owns
# the dirty-worktree rule. Identity on `True`, for the same reason
# `verification_gate`'s opt-out uses it: a step that answered `passed: "yes"`
# is broken, and reading that as green would ship unverified work.
def verification_passed_gate(result: object) -> dict[str, str] | None:
    """``None`` when the suite really passed, else a blocked verdict."""
    if not isinstance(result, Mapping):
        return {
            "blocked": "verification",
            "detail": (
                "no verification result to judge: the verify step returned "
                f"{_js_text(result)} instead of a result mapping, so nothing "
                "established that this subtask's tests are green."
            ),
        }
    if result.get("passed") is True:
        return None
    detail = str(result.get("detail") or "").strip()
    return {
        "blocked": "verification",
        "detail": detail
        or (
            "the verification suite did not pass and reported no detail "
            f"(result: {_json(dict(result))})"
        ),
    }


# The `implement` phase's gate (`TASK`), decision O7. The coder is
# told to REPORT `blocked: true` with a reason when it cannot proceed (a red
# baseline, a plan hash it could not compute) and never to decide what happens
# next. Without this gate `review` ran anyway and a subtask could reach `done`
# on partial work. Identity on `True`, as in `verification_passed_gate`:
# `ImplementResult` is validated upstream, so a real bool is guaranteed.
def implement_blocked_gate(result: object) -> dict[str, str] | None:
    """``None`` when the coder was not blocked, else a blocked verdict."""
    if not isinstance(result, Mapping):
        return {
            "blocked": "implement",
            "detail": (
                "no implement result to judge: the implement phase returned "
                f"{_js_text(result)} instead of a result mapping, so nothing "
                "established that the coder finished its work."
            ),
        }
    if result.get("blocked") is not True:
        return None
    detail = str(result.get("blocked_reason") or "").strip()
    return {
        "blocked": "implement",
        "detail": detail or "the coder reported blocked: true but gave no reason",
    }


# The `validate_spec` / `validate_plan` gate (`TASK`), ported from task.js
# lines 631-638 and 717-721. The critic is asked to
# REPORT whether the spec or the plan has unresolvable blockers and never to
# decide what to do about them, for the reason `review_gate` exists: an agent
# that both measures and judges can talk itself out of the judgement. One
# callable serves both phases -- the engine's own failure message already names
# which one stopped (`phase 'validate_plan' gate 'critic_blockers_gate'
# failed: ...`), so a per-phase `blocked` value would only duplicate it.
def critic_blockers_gate(result: object) -> dict[str, str] | None:
    """``None`` when the critic found no blockers, else a blocked verdict.

    ``result`` is the critic phase's own result: ``walk.gate_values``, which
    builds the gate table for both phase kinds, places it under exactly that
    key, which is why the parameter is not named after either phase.

    A dead validator -- ``None``, or anything that is not a ``Mapping`` -- is
    itself a block, checked before ``blockers`` rather than falling out of its
    falsiness. Silence is not consent: a validation phase that produced no
    judgement has not cleared the spec, and reading that as a pass is how an
    unvalidated plan reaches ``implement``.
    """
    if not isinstance(result, Mapping):
        return {"blocked": "validation", "detail": "the validator returned nothing"}
    if not _field(result, "blockers"):
        return None
    raw_reason = _field(result, "reason")
    # Mirrors JS `String(reason || '')`: any falsy value becomes the empty
    # string, and `_js_text` keeps a non-string readable as the harness JSON it
    # came from rather than as a Python repr.
    reason = ("" if not raw_reason else _js_text(raw_reason)).strip()
    return {
        "blocked": "validation",
        "detail": reason or "spec has unresolvable blockers",
    }


# The `review` phase's first gate (`TASK`), ported from task.js
# lines 842-855. The reviewer fixes what it can and REPORTS what is still
# standing in `unresolved_blockers`; before this gate nothing read that list,
# so a review that said "this is not done" still went on to `verify` and
# `done`. Only what is STILL standing gates: `findings` the reviewer already
# fixed do not. Listed before `review_gate` so the escalation names the
# reviewer's own judgement rather than a symptom of it.
def review_blockers_gate(result: object) -> dict[str, str] | None:
    """``None`` when the reviewer left nothing open, else a blocked verdict.

    A dead reviewer -- ``None``, or anything that is not a ``Mapping`` -- is
    itself a block, for the reason ``critic_blockers_gate`` gives: silence is
    not a clean review.
    """
    if not isinstance(result, Mapping):
        return {"blocked": "review", "detail": "the review stage returned nothing"}
    blockers = list(_field(result, "unresolved_blockers") or [])
    if not blockers:
        return None
    return {
        "blocked": "review",
        "detail": (
            f"review left {len(blockers)} unresolved blocker(s): "
            f"{'; '.join(map(str, blockers))}"
        ),
    }


def _plan_hash_of(result: object) -> object:
    """The `plan_hash` field of a dumped phase result, or `None`.

    `None` rather than an error for a missing or non-mapping result: a phase
    that was skipped, escalated or returned nothing has no hash to compare, and
    `reducers.plan_hash_gate` already answers `None` (pass) for anything that is
    not an 8-hex-character string.
    """
    return result.get("plan_hash") if isinstance(result, Mapping) else None


def plan_hash_gate_adapter(
    docs_commit: object = None, review: object = None
) -> dict[str, str] | None:
    """`reducers.plan_hash_gate` bound to the recorded hash and the review result.

    `walk.bind_arguments` binds whole values by parameter name only, so this
    takes the two phase names the table holds and does the one field lookup the
    binder cannot. Module-level, not a closure: `TASK`'s digest names it by
    `module.qualname`.

    Both parameters default to `None` so a run where `docs_commit` did not
    execute in this process still binds and passes.
    """
    return plan_hash_gate(_plan_hash_of(docs_commit), _plan_hash_of(review))
