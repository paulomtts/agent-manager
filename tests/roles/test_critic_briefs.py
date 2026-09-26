"""Golden briefs for the `spec_critic` and `plan_critic` roles (card c7bea6a2).

Placement: pygents-engine-design.md §9 (lines 399-400) tests roles as golden
briefs -- the shipped bundle is loaded through the public loader and its
system text is checked for what the validate phases depend on. No model call,
no git repository, no harness; same tier as `test_loader.py` and
`test_reviewer_brief.py`.
"""

import pytest

from agent_manager.roles.loader import RoleBundleError, load_role
from agent_manager.steps.plan_check import VALIDATED_MARKER

CRITICS = ("spec_critic", "plan_critic")

SHARED = ("Verify every suspicion", "Fold every CONFIRMED fix", "blockers=true only")

CRITERIA = {
    "spec_critic": (
        "completeness",
        "consistency",
        "clarity",
        "scope",
        "YAGNI",
        "sibling subtasks",
    ),
    "plan_critic": (
        "completeness",
        "spec alignment",
        "decomposition",
        "buildability",
        "traces back to the SPEC",
    ),
}


@pytest.mark.parametrize("role", CRITICS)
def test_critic_brief(role):
    text = load_role(role).system
    for needle in CRITERIA[role] + SHARED:
        assert needle in text, needle


def test_plan_critic_leaves_the_validated_marker_to_mark_validated():
    # plan_check.mark_validated (steps/plan_check.py:202) owns the marker; a
    # critic that wrote it would sign a plan the gate may still block.
    text = load_role("plan_critic").system
    assert VALIDATED_MARKER not in text
    assert "task-pipeline: validated" not in text


@pytest.mark.parametrize("role", CRITICS)
def test_critic_system_text_cannot_be_mistaken_for_brief_structure(role):
    # The system text opens every brief (prompt.compose_brief). These literals
    # are how the brief's own result contract and phase header are found
    # (tests/e2e/fake_claude.py:48), so the role must never carry them itself.
    text = load_role(role).system
    assert "## Result contract" not in text
    assert "```json" not in text
    assert not any(
        line.startswith(("# phase:", "# role:")) for line in text.splitlines()
    )


@pytest.mark.parametrize("role", CRITICS)
def test_critic_brief_has_no_unrendered_task_js_interpolation(role):
    assert "${" not in load_role(role).system


@pytest.mark.parametrize("role", CRITICS)
def test_critic_policy_is_the_generic_critic_policy_unchanged(role):
    # Copied byte-for-byte from the deleted critic bundle. The briefs say
    # "fold fixes into the file" yet no Edit/Write is granted: that gap is the
    # spec's open point for a human decision, so it is pinned, not resolved.
    bundle = load_role(role)
    assert bundle.policy.allowed_tools == ["Read", "Grep", "Glob"]
    assert bundle.policy.max_attempts == 1
    assert bundle.policy.required_capabilities == []
    assert bundle.policy.default_model == {"claude": "sonnet"}
    assert bundle.methodology == {}


def test_the_generic_critic_is_gone():
    with pytest.raises(RoleBundleError) as excinfo:
        load_role("critic")

    assert excinfo.value.reason == "no such role bundle"
