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
