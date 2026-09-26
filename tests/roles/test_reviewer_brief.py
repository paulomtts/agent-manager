"""Golden brief for the `reviewer` role (card ee3cc742).

Placement: pygents-engine-design.md §9 (lines 379-401) tests roles as golden
briefs -- the shipped bundle is loaded through the public loader and its
system text is checked for the instructions the review phase depends on. No
model call, no git repository, no harness, same tier as `test_loader.py`.
"""

from agent_manager.roles.loader import load_role


def test_reviewer_brief_carries_the_task_js_contract():
    role = load_role("reviewer")
    text = role.system
    for needle in (
        "git status --porcelain",
        "git rev-list --count",
        'grep -c "^Plan-Hash: $PLAN_HASH"',
        "Co-Authored-By:",
        "Plan-Hash: $PLAN_HASH",
        "sha256sum",
        "unresolved_blockers",
        "Never weaken, skip, xfail, or delete a test",
        "report their output verbatim",
    ):
        assert needle in text, needle
    assert {"Edit", "Write"} <= set(role.policy.allowed_tools)


THREE_COMMANDS = (
    "git status --porcelain",
    "git rev-list --count <base>..HEAD",
    'PLAN_HASH=$(sha256sum "<plan>" | cut -c1-8); '
    'git log <base>..HEAD --format=%B | grep -c "^Plan-Hash: $PLAN_HASH"',
)

RESULT_FIELDS = (
    "findings",
    "unresolved_blockers",
    "fix_summary",
    "porcelain",
    "commit_count",
    "tagged_count",
    "plan_hash",
)


def test_reviewer_brief_uses_agent_managers_inputs_and_result_fields():
    text = load_role("reviewer").system
    lines = [line.strip() for line in text.splitlines()]

    for command in THREE_COMMANDS:
        assert command in lines, command
    for needle in (
        "base_branch",
        "plan_path",
        "git diff <base>...HEAD",
        'PLAN_HASH=$(sha256sum "<plan>" | cut -c1-8)',
        "Co-Authored-By: Claude <noreply@anthropic.com>",
        *(f"`{field}`" for field in RESULT_FIELDS),
    ):
        assert needle in text, needle
    for leftover in (
        "git -C",
        "${",
        "unresolvedBlockers",
        "fixSummary",
        "commitCount",
        "taggedCount",
        "planHash",
    ):
        assert leftover not in text, leftover


def test_reviewer_system_text_cannot_be_mistaken_for_brief_structure():
    # The system text opens every review brief (prompt.compose_brief). These
    # literals are how the brief's own result contract and phase header are
    # found (prompt.py:339, :352; tests/e2e/fake_claude.py:48, :73, :79), so
    # the role must never carry them itself.
    text = load_role("reviewer").system
    assert "## Result contract" not in text
    assert "```json" not in text
    assert not any(
        line.startswith(("# phase:", "# role:")) for line in text.splitlines()
    )


def test_reviewer_policy_adds_edit_and_write_and_changes_nothing_else():
    policy = load_role("reviewer").policy
    assert policy.allowed_tools == ["Read", "Grep", "Glob", "Bash", "Edit", "Write"]
    assert policy.max_attempts == 1
    assert policy.required_capabilities == []
    assert policy.default_model == {"claude": "opus"}
