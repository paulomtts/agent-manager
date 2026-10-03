"""Golden briefs for the four document roles: they never commit (card c06c167b).

Placement: pygents-engine-design.md §9 (lines 402-403) tests roles as golden
briefs -- the shipped bundle is loaded through the public loader and its
system text is checked. The spec and the plan are committed by the workflow's
`docs_commit` step, so the roles that write or edit them must say they never
run `git commit`. No model call, no git repository, no harness: unit tier,
same as `test_critic_briefs.py` and `test_loader.py`.
"""

import pytest

from agent_manager.roles.loader import load_role

DOCUMENT_ROLES = ("spec_author", "planner", "spec_critic", "plan_critic")

DO_NOT_COMMIT = ("Never run `git commit`", "docs_commit", "Plan-Hash")

CRITIC_PARAGRAPH = {
    "spec_critic": (
        "Never run `git commit`. Folding fixes into the spec file is the whole"
        " job: the workflow's `docs_commit` step commits the spec and the plan"
        " together, with the `Plan-Hash` trailer that ties them to the finished"
        " plan."
    ),
    "plan_critic": (
        "Never run `git commit`. Folding fixes into the plan file is the whole"
        " job: the workflow's `docs_commit` step commits the spec and the plan"
        " together, with the `Plan-Hash` trailer that ties them to the finished"
        " plan."
    ),
}


@pytest.mark.parametrize("role", DOCUMENT_ROLES)
def test_document_roles_do_not_commit(role):
    text = load_role(role).system
    for needle in DO_NOT_COMMIT:
        assert needle in text, (role, needle)


def test_planner_keeps_the_commit_step_it_writes_for_the_engineer():
    # planner/system.md wraps the existing bullet as "a commit at the" /
    # "end of each task", so the needle stops before the line break.
    text = load_role("planner").system
    assert "a commit at the" in text
    assert "not one you run" in text


def test_spec_author_leaves_the_commit_steps_to_the_engineer():
    text = load_role("spec_author").system
    assert "are for the engineer who executes the plan" in text


@pytest.mark.parametrize("role", sorted(CRITIC_PARAGRAPH))
def test_critic_do_not_commit_paragraph_is_one_line(role):
    lines = load_role(role).system.splitlines()
    assert CRITIC_PARAGRAPH[role] in lines, role
