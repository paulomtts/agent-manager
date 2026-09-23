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

import json
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

    full_suite = _field(_field(explore, "verification"), "fullSuite")
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
