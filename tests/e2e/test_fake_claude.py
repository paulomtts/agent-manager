"""Tests for the fake `claude` of the production-wiring tier.

Tier follows what a test touches, not the directory it sits in (V1 of
`docs/superpowers/specs/2026-10-02-test-tier-design.md`, which supersedes the
design doc's old testing section). Each test here is marked on its own:

- `@pytest.mark.e2e_fake`: the tests that call `_run_fake`, which runs
  `fake_claude.py` as a child process. A test that also builds a git repo is
  still `e2e_fake` only: one test, one tier.
- `@pytest.mark.git`: the tests that build a real git repo in `tmp_path`
  through `_implement_repo`, directly or via `_review_worktree` or
  `_conflicted_repo`, and call the script's functions in-process.
- no marker: the pure parsing, schema and payload tests, in the default `unit`
  tier with its PATH shim and 0.5s budget.

This module is the one exception to the `tests/e2e/` directory auto-mark
(`_AUTO_MARK_EXEMPT` in `tests/conftest.py`). Every other module under
`tests/e2e/` drives production wiring, so `e2e_fake` is the right default
there. Most tests here only exercise the fake's helpers as plain functions;
auto-marking them would keep them out of the default run for no reason. They
stay in this file, rather than moving to a separate parsing module, because
they share its by-path load of the script and its brief and schema fixtures.
There is no module-level `pytestmark`, which would mark the pure tests too.
`test_each_test_here_carries_exactly_the_tier_its_helpers_touch` fails when a
test's marker disagrees with the helpers it reaches.

`tests/e2e/fake_claude.py` is a script, not a package module: it is copied to a
tmp directory and executed as `claude` by the production-wiring tier. It is
loaded here by path rather than imported by name, because `tests/e2e` is not on
`sys.path` under `--import-mode=importlib`.
"""

import ast
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_manager import dag
from agent_manager.steps.reducers import critic_blockers_gate, review_gate
from agent_manager.steps.integrate import merge_completed_gate

_SOURCE = Path(__file__).with_name("fake_claude.py")
_spec = importlib.util.spec_from_file_location("e2e_fake_claude", _SOURCE)
assert _spec is not None and _spec.loader is not None
fake_claude = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_claude)

_CONFTEST = Path(__file__).with_name("conftest.py")


def _conftest_constant(name):
    """A module-level literal from `tests/e2e/conftest.py`, read without importing it.

    `--import-mode=importlib` puts nothing on `sys.path`, so conftest names are
    not importable; parsing the file is how the twins are pinned to each other.
    """
    tree = ast.parse(_CONFTEST.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"tests/e2e/conftest.py defines no {name}")


_TIERS = frozenset({"git", "brd", "e2e_fake", "soak", "e2e"})
"""`tests/conftest.py`'s `TIER_MARKERS`, written out: that conftest is not
imported here, for the same reason `_conftest_constant` parses its sibling."""


def test_the_tier_set_here_is_the_root_conftests_tier_markers():
    """`_TIERS` is a hand copy; a tier added to `tests/conftest.py` and not here
    would be invisible to the marker guard below."""
    tree = ast.parse(
        (Path(__file__).parents[1] / "conftest.py").read_text(encoding="utf-8")
    )
    [value] = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "TIER_MARKERS"
            for target in node.targets
        )
    ]
    assert isinstance(value, ast.Call) and value.func.id == "frozenset"
    assert _TIERS == ast.literal_eval(value.args[0])


def _tier_marks(node):
    """The tier names among `node`'s `@pytest.mark.<name>` decorators, called or bare."""
    names = set()
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Attribute)
            and target.value.attr == "mark"
        ):
            names.add(target.attr)
    return names & _TIERS


def _expected_tiers():
    """Each test function in this module mapped to (the tiers it needs, the tiers it has).

    Needs `e2e_fake` when it reaches `_run_fake`, else `git` when it reaches
    `_implement_repo` (directly or through `_review_worktree`/`_conflicted_repo`),
    else no tier. Reaching is transitive over this module's top-level functions.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    calls = {
        name: {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in functions
        }
        for name, node in functions.items()
    }

    def reaches(start, target):
        seen, stack = {start}, [start]
        while stack:
            for callee in calls[stack.pop()]:
                if callee == target:
                    return True
                if callee not in seen:
                    seen.add(callee)
                    stack.append(callee)
        return False

    expected = {}
    for name, node in functions.items():
        if not name.startswith("test_"):
            continue
        if reaches(name, "_run_fake"):
            want = {"e2e_fake"}
        elif reaches(name, "_implement_repo"):
            want = {"git"}
        else:
            want = set()
        expected[name] = (want, _tier_marks(node))
    return expected


def test_each_test_here_carries_exactly_the_tier_its_helpers_touch():
    """This module is exempt from the `tests/e2e/` auto-mark, so its markers are
    the only thing keeping a process- or git-touching test out of the unit tier.
    `_run_fake` spawns by absolute path, which the unit PATH shim cannot catch."""
    expected = _expected_tiers()

    wrong = {
        name: f"needs {sorted(want)}, has {sorted(has)}"
        for name, (want, has) in expected.items()
        if want != has
    }

    assert wrong == {}
    wants = [want for want, _ in expected.values()]
    # Non-vacuity: the walk really finds all three kinds.
    assert {"e2e_fake"} in wants
    assert {"git"} in wants
    assert set() in wants


def test_the_prompt_path_is_parsed_out_of_the_adapters_p_sentence():
    argv = [
        "--model",
        "sonnet",
        "--dangerously-skip-permissions",
        "-p",
        "Read /tmp/run/explore.1/prompt.txt and follow the instructions in it "
        "exactly. It is your complete brief for this task.",
    ]

    assert fake_claude.prompt_path_from_argv(argv) == Path(
        "/tmp/run/explore.1/prompt.txt"
    )


def test_an_unrecognised_p_sentence_is_refused_rather_than_guessed():
    """Review focus: the adapter's sentence is a contract. A changed sentence
    must stop the fake, not make it read some other word as a path."""
    argv = ["-p", "do the thing described in /tmp/run/prompt.txt"]

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.prompt_path_from_argv(argv)

    assert "-p" in str(caught.value)


def test_argv_without_a_p_flag_is_refused():
    with pytest.raises(fake_claude.FakeClaudeError):
        fake_claude.prompt_path_from_argv(["--model", "sonnet"])


def test_the_phase_is_read_from_the_rendered_prompt_header():
    text = "# Reviewer\n\nstanding instructions\n\n# phase: review\n# role: reviewer\n\n## branch\nm1/task-x\n"

    assert fake_claude.phase_of(text) == "review"


CONTRACT = """# Explorer

standing instructions

## methodology: exploring.md

```json
{"not": "the schema"}
```

# phase: explore
# role: explorer

## repo_docs
path: /w/CLAUDE.md
2 line(s), shown in full:
## Verification
uv run pytest

## verification
[
  "uv run pytest"
]

## Result contract
When you are done, write your result as valid JSON to exactly this path:

/xdg/agent-manager/runs/r1/card/explore.1/result.json

That path is deliberately outside the worktree you are working in.

The JSON must validate against this schema:

```json
{
  "$defs": {
    "Verification": {
      "properties": {
        "full_suite": {"items": {"type": "string"}, "type": "array"},
        "typecheck": {"type": "string"},
        "lint": {"items": {"type": "string"}, "type": "array"}
      },
      "type": "object"
    }
  },
  "properties": {
    "refused": {"type": "boolean"},
    "reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "summary": {"type": "string"},
    "verification": {"$ref": "#/$defs/Verification"}
  },
  "type": "object"
}
```
"""


def test_the_result_path_comes_out_of_the_contract_section():
    assert fake_claude.result_path_of(CONTRACT) == Path(
        "/xdg/agent-manager/runs/r1/card/explore.1/result.json"
    )


def test_the_schema_is_the_fence_inside_the_contract_not_an_earlier_one():
    """A methodology section may carry its own ```json fence; only the fence
    after `## Result contract` is the schema the engine will validate against."""
    schema = fake_claude.schema_of(CONTRACT)

    assert sorted(schema["properties"]) == [
        "reason",
        "refused",
        "summary",
        "verification",
    ]


def test_a_brief_with_no_result_contract_is_refused():
    """Review focus: addendum R2. No contract means no result path, and the fake
    must say so rather than invent one."""
    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.result_path_of("# phase: explore\n# role: explorer\n\n## branch\nx\n")

    assert "Result contract" in str(caught.value)


def test_sections_are_read_case_sensitively_from_below_the_phase_header():
    """`repo_docs` inlines an excerpt of a repo CLAUDE.md, which may carry its
    own `## Verification` heading -- that must not shadow the `## verification`
    input the gate compares against."""
    found = fake_claude.sections(CONTRACT)

    assert json.loads(found["verification"]) == ["uv run pytest"]
    assert "Verification" in found
    assert found["Verification"] != found["verification"]


def test_a_payload_is_generated_from_the_schema_alone():
    payload = fake_claude.payload_from_schema(fake_claude.schema_of(CONTRACT))

    assert payload == {
        "refused": False,
        "reason": None,
        "summary": "",
        "verification": {"full_suite": [], "typecheck": "", "lint": []},
    }


def test_a_schema_node_with_no_default_is_refused_rather_than_nulled():
    """Review focus: the result models are `strict=True`, so a silently nulled
    field would surface as an unexplained `schema_invalid` two layers away."""
    with pytest.raises(fake_claude.FakeClaudeError):
        fake_claude.payload_from_schema(
            {"properties": {"weird": {"type": "tuple"}}, "type": "object"}
        )


def test_overriding_a_field_the_schema_does_not_have_is_refused():
    """Review focus: a renamed result-model field must stop the fake, not
    produce a payload whose gate silently reads `None`."""
    payload = {"summary": "", "blockers": False}

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.override(payload, findings=[])

    assert "findings" in str(caught.value)
    assert "blockers" in str(caught.value)


def _brief(tmp_path, phase, role, body, schema, result_path):
    """A brief shaped like `prompt.compose_brief`'s output, written to disk."""
    text = (
        f"# {role}\n\nstanding instructions\n\n"
        f"# phase: {phase}\n# role: {role}\n"
        f"{body}\n"
        f"{fake_claude.RESULT_HEADING}\n"
        "When you are done, write your result as valid JSON to exactly this path:\n"
        "\n"
        f"{result_path}\n"
        "\n"
        "That path is deliberately outside the worktree you are working in.\n"
        "\n"
        "The JSON must validate against this schema:\n"
        "\n"
        "```json\n"
        f"{json.dumps(schema, indent=2)}\n"
        "```\n"
    )
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text(text, encoding="utf-8")
    return prompt_path


CRITIC_SCHEMA = {
    "properties": {
        "blockers": {"type": "boolean"},
        "reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "summary": {"type": "string"},
    },
    "type": "object",
}


def _run_fake(prompt_path, cwd):
    return subprocess.run(
        [
            sys.executable,
            str(_SOURCE),
            "--model",
            "sonnet",
            "--dangerously-skip-permissions",
            "-p",
            f"Read {prompt_path} and follow the instructions in it exactly. "
            "It is your complete brief for this task.",
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


@pytest.mark.e2e_fake
def test_the_fake_writes_a_gate_passing_critic_result_where_the_brief_says(tmp_path):
    """Production-wiring tier's own fixture check: the script end to end, driven
    only by a brief on disk."""
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.1"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path,
        "validate_spec",
        "spec_critic",
        "\n## spec_path\ndocs/superpowers/specs/x-00000001.md\n",
        CRITIC_SCHEMA,
        result_path,
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["blockers"] is False
    assert payload["reason"] is None
    assert len(payload["summary"]) > 60


@pytest.mark.e2e_fake
def test_the_fake_logs_its_phase_and_cwd_beside_the_run_directory(tmp_path):
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.1"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path, "validate_spec", "spec_critic", "\n## spec_path\nx.md\n",
        CRITIC_SCHEMA, result_path,
    )
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    completed = _run_fake(prompt_path, workdir)

    assert completed.returncode == 0, completed.stderr
    log = tmp_path / "runs" / "r1" / fake_claude.LOG_NAME
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["phase"] == "validate_spec"
    assert Path(entry["cwd"]).resolve() == workdir.resolve()
    assert entry["result_path"] == str(result_path)


@pytest.mark.e2e_fake
def test_a_brief_without_a_result_contract_makes_the_fake_exit_non_zero(tmp_path):
    """Review focus / addendum R2: no contract, no run. This is what makes the
    production-wiring test fail loudly if prompt composition ever regresses."""
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text(
        "# Spec critic\n\n# phase: validate_spec\n# role: spec_critic\n\n## spec_path\nx.md\n",
        encoding="utf-8",
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 1
    assert "Result contract" in completed.stderr


@pytest.mark.e2e_fake
def test_a_feedback_block_after_the_contract_does_not_hide_the_contract(tmp_path):
    """Review focus: `dispatch._append_feedback` appends
    `## feedback on the previous attempt` AFTER the contract on a retry."""
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.2"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path, "validate_spec", "spec_critic", "\n## spec_path\nx.md\n",
        CRITIC_SCHEMA, result_path,
    )
    prompt_path.write_text(
        prompt_path.read_text(encoding="utf-8")
        + "\n## feedback on the previous attempt\nthe result file was missing\n",
        encoding="utf-8",
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(result_path.read_text(encoding="utf-8"))["blockers"] is False


IMPLEMENT_SCHEMA = {
    "properties": {
        "blocked": {"type": "boolean"},
        "blocked_reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "resumed": {"type": "boolean"},
        "plan_hash": {"type": "string"},
        "report": {"type": "string"},
    },
    "type": "object",
}
"""`results.ImplementResult`'s shape, written out here rather than imported:
the fake is a standalone script and learns a schema only from a brief."""

PLAN_RELATIVE = "docs/superpowers/plans/x-00000001.md"
BRIEF_HASH = "0badcafe"
"""Deliberately NOT the sha256 of the plan file the fixture writes. The
disagreement is what proves the fake reads the brief rather than hashing."""


def _implement_repo(tmp_path):
    """A real git repo with a committed plan file, as `implement` finds one."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True
    )
    for key, value in (
        ("user.email", "tests@example.com"),
        ("user.name", "agent-manager tests"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(
            ["git", "-C", str(root), "config", key, value],
            check=True,
            capture_output=True,
        )
    plan = root / PLAN_RELATIVE
    plan.parent.mkdir(parents=True)
    plan.write_text("# plan\n\nvalidated: yes\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "docs: the spec and the plan"],
        check=True,
        capture_output=True,
    )
    return root


def _implement_brief_text(digest=None, plan=PLAN_RELATIVE):
    """An `implement` brief, optionally without its `## plan_hash` section."""
    section = "" if digest is None else f"\n## plan_hash\n{digest}\n"
    return (
        "# Coder\n\nstanding instructions\n\n"
        "# phase: implement\n# role: coder\n"
        f"\n## plan_path\n{plan}\n"
        f"{section}"
    )


def _head_message(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "show", "-s", "--format=%B", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.mark.git
def test_the_fake_coder_takes_its_trailer_hash_from_the_brief_not_the_plan_file(
    tmp_path,
):
    """R4: the fake may know only what the brief says. It must not hash the plan."""
    repo = _implement_repo(tmp_path)
    on_disk = fake_claude.plan_hash_of(repo / PLAN_RELATIVE)
    assert on_disk != BRIEF_HASH  # non-vacuity: the two really do disagree

    payload = fake_claude.build_result(
        "implement",
        fake_claude.payload_from_schema(IMPLEMENT_SCHEMA),
        _implement_brief_text(BRIEF_HASH),
        repo,
    )

    assert payload["plan_hash"] == BRIEF_HASH
    message = _head_message(repo)
    assert message.rstrip("\n").endswith(f"Plan-Hash: {BRIEF_HASH}")
    assert on_disk not in message


@pytest.mark.git
def test_an_implement_brief_with_no_plan_hash_section_stops_the_fake(tmp_path):
    """The mechanism that makes the production-wiring test fail if the input is
    ever dropped from `implement`'s `inputs` in `builtin/task.yaml`."""
    repo = _implement_repo(tmp_path)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.build_result(
            "implement",
            fake_claude.payload_from_schema(IMPLEMENT_SCHEMA),
            _implement_brief_text(),
            repo,
        )

    assert "plan_hash" in str(caught.value)
    assert "implement" in str(caught.value)


@pytest.mark.git
def test_a_padded_plan_hash_section_still_produces_a_single_line_trailer(tmp_path):
    """Review focus: a body padded with blank lines must not end the commit
    message in a blank `Plan-Hash:` line that `review_gate` reads as debris."""
    repo = _implement_repo(tmp_path)
    text = _implement_brief_text(BRIEF_HASH).replace(
        f"\n## plan_hash\n{BRIEF_HASH}\n", f"\n## plan_hash\n\n{BRIEF_HASH}\n\n"
    )

    payload = fake_claude.build_result(
        "implement", fake_claude.payload_from_schema(IMPLEMENT_SCHEMA), text, repo
    )

    assert payload["plan_hash"] == BRIEF_HASH
    assert _head_message(repo).rstrip("\n").splitlines()[-1] == (
        f"Plan-Hash: {BRIEF_HASH}"
    )


def _head(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _porcelain(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _implement(repo, plan=PLAN_RELATIVE):
    return fake_claude.build_result(
        "implement",
        fake_claude.payload_from_schema(IMPLEMENT_SCHEMA),
        _implement_brief_text(BRIEF_HASH, plan),
        repo,
    )


@pytest.mark.git
def test_a_stacked_subtask_with_its_own_plan_commits_its_own_implementation(tmp_path):
    """Review focus: a milestone stacks a2 on a1's branch, so a2's worktree already
    holds a1's implementation file. The file's content names the brief's plan
    path, so a2's coder still has something to commit."""
    repo = _implement_repo(tmp_path)
    first = _implement(repo)
    after_first = _head(repo)

    second = _implement(repo, plan="docs/superpowers/plans/y-00000002.md")

    assert first["resumed"] is False
    assert second["resumed"] is False
    assert _head(repo) != after_first
    assert _head_message(repo).rstrip("\n").endswith(f"Plan-Hash: {BRIEF_HASH}")
    assert _porcelain(repo) == ""


@pytest.mark.git
def test_a_second_implement_on_the_same_plan_resumes_instead_of_failing(tmp_path):
    """Review focus / spec "Re-entering B": a relaunched subtask's implementation
    is already committed. The fake reports `resumed` rather than dying on
    "nothing to commit", and it still takes its hash from the brief."""
    repo = _implement_repo(tmp_path)
    _implement(repo)
    committed = _head(repo)

    again = _implement(repo)

    assert again["resumed"] is True
    assert again["plan_hash"] == BRIEF_HASH
    assert _head(repo) == committed
    assert _porcelain(repo) == ""


REVIEW_SCHEMA = {
    "properties": {
        "findings": {"items": {"type": "string"}, "type": "array"},
        "unresolved_blockers": {"items": {"type": "string"}, "type": "array"},
        "fix_summary": {"type": "string"},
        "porcelain": {"type": "string"},
        "commit_count": {"type": "integer"},
        "tagged_count": {"type": "integer"},
        "plan_hash": {"type": "string"},
    },
    "type": "object",
}
"""`results.ReviewResult`'s shape, written out for the same reason as
`IMPLEMENT_SCHEMA`."""

REVIEW_BRANCH = "m3/task-b1-00000003"


def _review_worktree(tmp_path):
    """A linked worktree on `REVIEW_BRANCH` with one tagged commit, as review finds it.

    Linked, not the main checkout, because a subtask's agents run in
    `<repo>/.claude/worktrees/<branch>`, where `.git` is a file and not the
    directory the marker lives in.
    """
    repo = _implement_repo(tmp_path)
    worktree = tmp_path / "worktree"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", str(worktree), "-b", REVIEW_BRANCH],
        check=True,
        capture_output=True,
    )
    _implement(worktree)
    return repo, worktree


def _review(worktree, branch=REVIEW_BRANCH):
    text = (
        "# Reviewer\n\nstanding instructions\n\n"
        "# phase: review\n# role: reviewer\n"
        f"\n## branch\n{branch}\n"
        "\n## base_branch\nmain\n"
        f"\n## plan_path\n{PLAN_RELATIVE}\n"
        f"\n## plan_hash\n{BRIEF_HASH}\n"
    )
    return fake_claude.build_result(
        "review", fake_claude.payload_from_schema(REVIEW_SCHEMA), text, worktree
    )


def test_the_review_fail_marker_name_is_the_one_the_e2e_fixtures_write():
    """`tests/e2e/conftest.py` writes `FAKE_REVIEW_FAIL_MARKER`; the script and the
    fixture meet across a process boundary, like `LOG_NAME`."""
    assert fake_claude.REVIEW_FAIL_MARKER == "fake-claude-review-fail"


@pytest.mark.git
def test_without_a_marker_the_review_passes_the_real_review_gate(tmp_path):
    _, worktree = _review_worktree(tmp_path)

    payload = _review(worktree)

    assert payload["findings"] == []
    assert payload["unresolved_blockers"] == []
    assert payload["porcelain"] == ""
    assert payload["commit_count"] == payload["tagged_count"] == 1
    assert review_gate(payload, REVIEW_BRANCH, "main") is None


@pytest.mark.git
def test_a_marker_naming_the_briefs_branch_makes_a_review_the_real_gate_blocks(tmp_path):
    """Spec: the trigger is the brief's `## branch` matched against a marker in the
    repo's git common dir. The failing result is schema-shaped (every field went
    through `override`) and `review_gate` blocks it on `porcelain`."""
    repo, worktree = _review_worktree(tmp_path)
    marker = repo / ".git" / fake_claude.REVIEW_FAIL_MARKER
    marker.write_text(f"m3/task-other-00000009\n{REVIEW_BRANCH}\n", encoding="utf-8")

    payload = _review(worktree)

    assert sorted(payload) == sorted(REVIEW_SCHEMA["properties"])
    assert payload["porcelain"] == fake_claude.REVIEW_FAIL_PORCELAIN
    assert "review-fail marker" in payload["porcelain"]
    assert payload["unresolved_blockers"]
    verdict = review_gate(payload, REVIEW_BRANCH, "main")
    assert verdict is not None and "blocked" in verdict
    # The marker lives outside every worktree's tree.
    assert _porcelain(worktree) == ""
    assert _porcelain(repo) == ""


@pytest.mark.git
def test_a_marker_naming_only_other_branches_does_not_fail_this_review(tmp_path):
    """Whole-line equality, never a prefix test: a branch that merely starts with
    this one's name must not fail it."""
    repo, worktree = _review_worktree(tmp_path)
    (repo / ".git" / fake_claude.REVIEW_FAIL_MARKER).write_text(
        f"{REVIEW_BRANCH}-later\n", encoding="utf-8"
    )

    payload = _review(worktree)

    assert payload["porcelain"] == ""
    assert review_gate(payload, REVIEW_BRANCH, "main") is None


def test_the_rendezvous_env_var_names_are_the_ones_the_e2e_fixtures_set():
    """`tests/e2e/conftest.py` sets `FAKE_RENDEZVOUS_DIR_ENV` and
    `FAKE_RENDEZVOUS_COUNT_ENV`; the fixture and the script meet across a
    process boundary, like `LOG_NAME` and `REVIEW_FAIL_MARKER`."""
    assert fake_claude.RENDEZVOUS_DIR_ENV == "FAKE_CLAUDE_RENDEZVOUS_DIR"
    assert fake_claude.RENDEZVOUS_COUNT_ENV == "FAKE_CLAUDE_RENDEZVOUS_COUNT"
    assert fake_claude.RENDEZVOUS_TIMEOUT == 20


def test_a_rendezvous_marker_name_is_stable_per_cwd_and_filesystem_safe(tmp_path):
    first = tmp_path / "worktrees" / "m3" / "task-a1-00000001"
    second = tmp_path / "worktrees" / "m3" / "task-b1-00000002"

    name = fake_claude.rendezvous_marker_name(first)

    assert name == fake_claude.rendezvous_marker_name(first)
    assert name != fake_claude.rendezvous_marker_name(second)
    assert name.endswith(fake_claude.RENDEZVOUS_SUFFIX)
    stem = name[: -len(fake_claude.RENDEZVOUS_SUFFIX)]
    assert len(stem) == 16 and set(stem) <= set("0123456789abcdef")


@pytest.mark.git
def test_without_a_rendezvous_dir_implement_neither_waits_nor_writes_a_marker(
    tmp_path, monkeypatch
):
    """Unset means today's behaviour exactly. The count is set to something
    unmeetable and the timeout is short, so any wait would raise."""
    monkeypatch.delenv(fake_claude.RENDEZVOUS_DIR_ENV, raising=False)
    monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, "99")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)
    repo = _implement_repo(tmp_path)
    before = sorted(path.name for path in tmp_path.iterdir())

    payload = _implement(repo)

    assert payload["resumed"] is False
    assert sorted(path.name for path in tmp_path.iterdir()) == before


@pytest.mark.git
def test_a_met_rendezvous_writes_a_marker_named_for_the_cwd_and_implements(
    tmp_path, monkeypatch
):
    """Review focus: the dir does not exist yet, and the fake creates it."""
    folder = tmp_path / "rendezvous" / "created-by-the-fake"
    monkeypatch.setenv(fake_claude.RENDEZVOUS_DIR_ENV, str(folder))
    monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, "1")
    repo = _implement_repo(tmp_path)
    before = _head(repo)

    payload = _implement(repo)

    marker = folder / fake_claude.rendezvous_marker_name(repo)
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8").strip() == str(repo)
    assert payload["resumed"] is False
    assert payload["plan_hash"] == BRIEF_HASH
    assert _head(repo) != before


@pytest.mark.git
def test_a_rendezvous_counts_markers_other_lanes_left(tmp_path, monkeypatch):
    """Count 2 with one peer marker already present: the second arrival passes
    straight through, which is how two lanes release each other."""
    folder = tmp_path / "rendezvous"
    folder.mkdir()
    (folder / ("0" * 16 + fake_claude.RENDEZVOUS_SUFFIX)).write_text(
        "peer\n", encoding="utf-8"
    )
    monkeypatch.setenv(fake_claude.RENDEZVOUS_DIR_ENV, str(folder))
    monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, "2")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 5.0)
    repo = _implement_repo(tmp_path)

    payload = _implement(repo)

    assert payload["resumed"] is False
    assert len(list(folder.glob(f"*{fake_claude.RENDEZVOUS_SUFFIX}"))) == 2


@pytest.mark.git
def test_an_unmet_rendezvous_fails_the_fake_before_it_commits(tmp_path, monkeypatch):
    folder = tmp_path / "rendezvous"
    monkeypatch.setenv(fake_claude.RENDEZVOUS_DIR_ENV, str(folder))
    monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, "2")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)
    repo = _implement_repo(tmp_path)
    before = _head(repo)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _implement(repo)

    message = str(caught.value)
    assert str(folder) in message
    assert "saw 1 of 2" in message
    assert _head(repo) == before


@pytest.mark.git
def test_the_same_cwd_arriving_twice_counts_once(tmp_path, monkeypatch):
    """Review focus: a retried implement in one worktree must not satisfy a
    count of 2 on its own, or a single lane would fake an overlap."""
    folder = tmp_path / "rendezvous"
    monkeypatch.setenv(fake_claude.RENDEZVOUS_DIR_ENV, str(folder))
    monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, "2")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)
    repo = _implement_repo(tmp_path)

    for _ in range(2):
        with pytest.raises(fake_claude.FakeClaudeError):
            fake_claude.rendezvous(repo)

    assert len(list(folder.glob(f"*{fake_claude.RENDEZVOUS_SUFFIX}"))) == 1


@pytest.mark.parametrize("raw", [None, "", "two", "1.5", "0", "-1"])
def test_a_missing_or_bad_rendezvous_count_is_refused(tmp_path, monkeypatch, raw):
    """Review focus: a count below 1 would pass silently, and a non-number
    would be a guess; both are refusals naming the env var."""
    monkeypatch.setenv(fake_claude.RENDEZVOUS_DIR_ENV, str(tmp_path / "rendezvous"))
    if raw is None:
        monkeypatch.delenv(fake_claude.RENDEZVOUS_COUNT_ENV, raising=False)
    else:
        monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, raw)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.rendezvous(tmp_path)

    assert fake_claude.RENDEZVOUS_COUNT_ENV in str(caught.value)


@pytest.mark.e2e_fake
def test_a_rendezvous_failure_makes_the_fake_process_exit_1(tmp_path, monkeypatch):
    """The `__main__` mapping, end to end: the child inherits the env (as it does
    under `launcher.run_direct`), refuses the count, writes no result."""
    monkeypatch.setenv(fake_claude.RENDEZVOUS_DIR_ENV, str(tmp_path / "rendezvous"))
    monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, "two")
    attempt = tmp_path / "runs" / "r1" / "card" / "implement.1"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path,
        "implement",
        "coder",
        f"\n## plan_path\n{PLAN_RELATIVE}\n\n## plan_hash\n{BRIEF_HASH}\n",
        IMPLEMENT_SCHEMA,
        result_path,
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 1
    assert "FAKE_CLAUDE_RENDEZVOUS_COUNT" in completed.stderr
    assert not result_path.exists()


HOLD_CARD = "0123abcd-4567-89ef-0123-456789abcdef"
"""A card id shaped like brd's, so the fake's `short_id` accepts it."""

HOLD_SHORT = "0123abcd"
"""`dag.short_id(HOLD_CARD)`, written out so a drift in either shows."""


def _hold_result_path(tmp_path, phase="implement"):
    """`<run dir>/<card id>/<phase>.<n>/result.json`, the shape `paths.attempt_dir` gives."""
    return tmp_path / "runs" / "r1" / HOLD_CARD / f"{phase}.1" / "result.json"


def _arm_hold(monkeypatch, tmp_path, phase=None):
    """Point the hold at `<tmp>/hold`; `phase=None` leaves the default phase."""
    folder = tmp_path / "hold"
    monkeypatch.setenv(fake_claude.HOLD_DIR_ENV, str(folder))
    if phase is None:
        monkeypatch.delenv(fake_claude.HOLD_PHASE_ENV, raising=False)
    else:
        monkeypatch.setenv(fake_claude.HOLD_PHASE_ENV, phase)
    return folder


def test_the_hold_names_are_pinned_and_are_the_conftest_twins():
    """The fixture and the script meet across a process boundary, like the
    rendezvous names above."""
    assert fake_claude.HOLD_DIR_ENV == "FAKE_CLAUDE_HOLD_DIR"
    assert fake_claude.HOLD_PHASE_ENV == "FAKE_CLAUDE_HOLD_PHASE"
    assert fake_claude.HOLD_DEFAULT_PHASE == "implement"
    assert fake_claude.HOLD_SUFFIX == ".held"
    assert fake_claude.RELEASE_SUFFIX == ".release"
    assert fake_claude.HOLD_DIR_ENV == _conftest_constant("FAKE_HOLD_DIR_ENV")
    assert fake_claude.HOLD_PHASE_ENV == _conftest_constant("FAKE_HOLD_PHASE_ENV")
    assert fake_claude.HOLD_SUFFIX == _conftest_constant("FAKE_HOLD_SUFFIX")
    assert fake_claude.RELEASE_SUFFIX == _conftest_constant("FAKE_RELEASE_SUFFIX")


def test_the_fakes_short_id_is_dags():
    """The test names a marker with `dag.short_id`, the fake with its own copy."""
    for card in (HOLD_CARD, HOLD_CARD.upper(), HOLD_CARD.replace("-", "")):
        assert fake_claude.short_id(card) == dag.short_id(card) == HOLD_SHORT


def test_a_result_path_that_names_no_card_is_refused_by_the_hold(tmp_path, monkeypatch):
    _arm_hold(monkeypatch, tmp_path)
    result_path = tmp_path / "runs" / "r1" / "not-a-card" / "implement.1" / "result.json"

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.hold("implement", result_path)

    assert "not-a-card" in str(caught.value)


def test_without_a_hold_dir_nothing_is_held(tmp_path, monkeypatch):
    """Unset means today's behaviour exactly; a wait would raise at 0.2s."""
    monkeypatch.delenv(fake_claude.HOLD_DIR_ENV, raising=False)
    monkeypatch.setenv(fake_claude.HOLD_PHASE_ENV, "implement")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)

    fake_claude.hold("implement", _hold_result_path(tmp_path))

    assert not (tmp_path / "hold").exists()


def test_a_hold_for_another_phase_passes_straight_through(tmp_path, monkeypatch):
    folder = _arm_hold(monkeypatch, tmp_path, phase="plan")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)

    fake_claude.hold("implement", _hold_result_path(tmp_path))

    assert not folder.exists()


def test_an_unknown_hold_phase_is_refused_on_any_phase(tmp_path, monkeypatch):
    """Review focus: a typo must stop the fake, not read as "never hold"."""
    _arm_hold(monkeypatch, tmp_path, phase="implemnt")

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.hold("explore", _hold_result_path(tmp_path, "explore"))

    assert fake_claude.HOLD_PHASE_ENV in str(caught.value)
    assert "implemnt" in str(caught.value)


def test_a_released_hold_writes_its_pid_marker_and_returns(tmp_path, monkeypatch):
    """Review focus: the marker holds this process's whole pid, and the
    atomic write leaves no temp file behind."""
    folder = _arm_hold(monkeypatch, tmp_path)
    folder.mkdir()
    (folder / f"{HOLD_SHORT}.release").write_text("", encoding="utf-8")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)

    fake_claude.hold("implement", _hold_result_path(tmp_path))

    marker = folder / f"{HOLD_SHORT}.held"
    assert marker.read_text(encoding="utf-8").strip() == str(os.getpid())
    assert sorted(path.name for path in folder.iterdir()) == [
        f"{HOLD_SHORT}.held",
        f"{HOLD_SHORT}.release",
    ]


def test_a_hold_waits_until_its_release_appears(tmp_path, monkeypatch):
    """The `.held` marker is written before the wait, and the wait ends only
    when the release is written by someone else."""
    folder = _arm_hold(monkeypatch, tmp_path)
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 30.0)
    marker = folder / f"{HOLD_SHORT}.held"
    saw_marker = threading.Event()

    def release_once_held():
        deadline = time.monotonic() + 30.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if marker.exists():
            saw_marker.set()
        (folder / f"{HOLD_SHORT}.release").write_text("", encoding="utf-8")

    releaser = threading.Thread(target=release_once_held)
    releaser.start()
    try:
        fake_claude.hold("implement", _hold_result_path(tmp_path))
        # Read before joining: a hold that returned without waiting would
        # come back before the releaser had written anything.
        released_on_return = (folder / f"{HOLD_SHORT}.release").exists()
    finally:
        releaser.join(timeout=30.0)

    assert saw_marker.is_set()
    assert released_on_return


def test_a_named_hold_phase_holds_that_phase(tmp_path, monkeypatch):
    folder = _arm_hold(monkeypatch, tmp_path, phase="plan")
    folder.mkdir()
    (folder / f"{HOLD_SHORT}.release").write_text("", encoding="utf-8")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)

    fake_claude.hold("plan", _hold_result_path(tmp_path, "plan"))

    assert (folder / f"{HOLD_SHORT}.held").is_file()


def test_an_unreleased_hold_times_out_naming_the_release_file(tmp_path, monkeypatch):
    folder = _arm_hold(monkeypatch, tmp_path)
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.hold("implement", _hold_result_path(tmp_path))

    message = str(caught.value)
    assert "timed out" in message
    assert str(folder / f"{HOLD_SHORT}.release") in message
    assert (folder / f"{HOLD_SHORT}.held").is_file()


@pytest.mark.e2e_fake
def test_the_fake_process_holds_before_it_implements(tmp_path, monkeypatch):
    """`main` wires the hold in: the child writes its marker, finds the
    release, then implements and writes its result."""
    folder = _arm_hold(monkeypatch, tmp_path)
    folder.mkdir()
    (folder / f"{HOLD_SHORT}.release").write_text("", encoding="utf-8")
    repo = _implement_repo(tmp_path)
    before = _head(repo)
    result_path = _hold_result_path(tmp_path)
    result_path.parent.mkdir(parents=True)
    prompt_path = _brief(
        tmp_path,
        "implement",
        "coder",
        f"\n## plan_path\n{PLAN_RELATIVE}\n\n## plan_hash\n{BRIEF_HASH}\n",
        IMPLEMENT_SCHEMA,
        result_path,
    )

    completed = _run_fake(prompt_path, repo)

    assert completed.returncode == 0, completed.stderr
    assert int((folder / f"{HOLD_SHORT}.held").read_text(encoding="utf-8")) > 0
    assert json.loads(result_path.read_text(encoding="utf-8"))["plan_hash"] == BRIEF_HASH
    assert _head(repo) != before


IMPLEMENT_BRANCH = "m3/task-a1-00000001"
OTHER_BRANCH = "m3/task-b1-00000002"


def _implement_on_branch(repo, branch=IMPLEMENT_BRANCH):
    """An implement brief that carries `## branch`, as `builtin/task.yaml` renders it."""
    text = _implement_brief_text(BRIEF_HASH) + f"\n## branch\n{branch}\n"
    return fake_claude.build_result(
        "implement", fake_claude.payload_from_schema(IMPLEMENT_SCHEMA), text, repo
    )


def _write_edits(repo, table):
    (repo / ".git" / fake_claude.IMPLEMENT_EDITS_MARKER).write_text(
        json.dumps(table), encoding="utf-8"
    )


def _show(repo, spec):
    return subprocess.run(
        ["git", "-C", str(repo), "show", spec],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_the_implement_edits_marker_name_is_the_conftest_twin():
    """The fixture writes `FAKE_IMPLEMENT_EDITS_MARKER`; the script reads
    `IMPLEMENT_EDITS_MARKER`. They meet across a process boundary."""
    assert fake_claude.IMPLEMENT_EDITS_MARKER == "fake-claude-implement-edits"
    assert fake_claude.IMPLEMENT_EDITS_MARKER == _conftest_constant(
        "FAKE_IMPLEMENT_EDITS_MARKER"
    )


@pytest.mark.git
def test_the_marker_entry_for_the_briefs_branch_is_written_and_committed(tmp_path):
    repo = _implement_repo(tmp_path)
    _write_edits(
        repo,
        {
            IMPLEMENT_BRANCH: {"shared.txt": "story A\n", "pkg/nested.txt": "deep\n"},
            OTHER_BRANCH: {"shared.txt": "story B\n"},
        },
    )

    payload = _implement_on_branch(repo)

    assert payload["resumed"] is False
    assert _show(repo, "HEAD:shared.txt") == "story A\n"
    assert _show(repo, "HEAD:pkg/nested.txt") == "deep\n"
    assert _show(repo, f"HEAD:{fake_claude.IMPLEMENTATION_NAME}")  # still written
    assert _porcelain(repo) == ""


@pytest.mark.git
def test_a_marker_entry_for_another_branch_writes_nothing_extra(tmp_path):
    repo = _implement_repo(tmp_path)
    _write_edits(repo, {OTHER_BRANCH: {"shared.txt": "story B\n"}})

    payload = _implement_on_branch(repo)

    assert payload["resumed"] is False
    assert not (repo / "shared.txt").exists()
    assert _porcelain(repo) == ""


@pytest.mark.git
def test_a_marker_with_no_branch_section_in_the_brief_stops_the_fake(tmp_path):
    """Review focus: the edits are keyed by the brief's `## branch`. A brief
    without it must fail loudly, never quietly skip the edits."""
    repo = _implement_repo(tmp_path)
    _write_edits(repo, {IMPLEMENT_BRANCH: {"shared.txt": "story A\n"}})
    before = _head(repo)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _implement(repo)

    assert "branch" in str(caught.value)
    assert _head(repo) == before
    assert _porcelain(repo) == ""


@pytest.mark.git
@pytest.mark.parametrize("relative", ["../outside.txt", "/tmp/absolute.txt", ""])
def test_a_marker_path_outside_the_worktree_is_refused_before_any_write(
    tmp_path, relative
):
    """Review focus: a typo in a test must not write outside the worktree."""
    repo = _implement_repo(tmp_path)
    _write_edits(repo, {IMPLEMENT_BRANCH: {relative: "nope\n"}})
    before = _head(repo)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _implement_on_branch(repo)

    assert "not a path inside the worktree" in str(caught.value)
    assert _head(repo) == before
    assert _porcelain(repo) == ""
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.git
def test_a_marker_that_is_not_json_is_refused(tmp_path):
    repo = _implement_repo(tmp_path)
    (repo / ".git" / fake_claude.IMPLEMENT_EDITS_MARKER).write_text(
        "{not json", encoding="utf-8"
    )

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _implement_on_branch(repo)

    assert "not valid JSON" in str(caught.value)


@pytest.mark.git
@pytest.mark.parametrize("table", [[], "a string", 1])
def test_a_marker_that_is_not_an_object_of_branches_is_refused(tmp_path, table):
    repo = _implement_repo(tmp_path)
    _write_edits(repo, table)
    before = _head(repo)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _implement_on_branch(repo)

    assert "not a JSON object of branches" in str(caught.value)
    assert _head(repo) == before
    assert _porcelain(repo) == ""


@pytest.mark.git
@pytest.mark.parametrize(
    "entry", [["shared.txt"], "shared.txt", {"shared.txt": 1}, {"shared.txt": None}]
)
def test_a_marker_entry_that_is_not_path_to_content_strings_is_refused(
    tmp_path, entry
):
    repo = _implement_repo(tmp_path)
    _write_edits(repo, {IMPLEMENT_BRANCH: entry})
    before = _head(repo)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _implement_on_branch(repo)

    assert "is not an object of path -> content strings" in str(caught.value)
    assert _head(repo) == before
    assert _porcelain(repo) == ""
    assert not (repo / "shared.txt").exists()


def test_both_sides_of_a_hunk_are_kept_ours_then_theirs():
    text = "top\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> side\nbottom\n"

    assert fake_claude.keep_both_sides(text) == "top\nours\ntheirs\nbottom\n"


def test_a_diff3_base_section_is_dropped():
    text = (
        "<<<<<<< HEAD\nours\n||||||| merged common ancestors\nbase\n"
        "=======\ntheirs\n>>>>>>> side\n"
    )

    assert fake_claude.keep_both_sides(text) == "ours\ntheirs\n"


def test_every_hunk_in_a_file_is_rewritten():
    text = (
        "<<<<<<< HEAD\none\n=======\nuno\n>>>>>>> side\n"
        "middle\n"
        "<<<<<<< HEAD\ntwo\n=======\ndos\n>>>>>>> side\n"
    )

    assert fake_claude.keep_both_sides(text) == "one\nuno\nmiddle\ntwo\ndos\n"


def test_an_underline_outside_a_hunk_is_kept():
    """Review focus: `=======` is a markdown/rst underline as often as a marker;
    it only counts inside a hunk."""
    text = "Title\n=======\n\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> side\n"

    assert fake_claude.keep_both_sides(text) == "Title\n=======\n\nours\ntheirs\n"


def test_crlf_lines_keep_their_endings():
    text = "<<<<<<< HEAD\r\nours\r\n=======\r\ntheirs\r\n>>>>>>> side\r\n"

    assert fake_claude.keep_both_sides(text) == "ours\r\ntheirs\r\n"


def test_a_file_with_no_hunk_is_unchanged():
    assert fake_claude.keep_both_sides("plain\ntext\n") == "plain\ntext\n"


def test_an_unclosed_hunk_is_refused_rather_than_truncated():
    """Review focus: half a rewrite would commit a file missing its tail."""
    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.keep_both_sides("<<<<<<< HEAD\nours\n=======\ntheirs\n")

    assert "never closed" in str(caught.value)


RESOLVE_SCHEMA = {
    "properties": {
        "resolved": {"type": "boolean"},
        "summary": {"type": "string"},
    },
    "type": "object",
}
"""`results.ResolveResult`'s shape, written out for the same reason as
`IMPLEMENT_SCHEMA`."""

SIDE_BRANCH = "m3/task-b1-00000002"
CONFLICT_FILES = '[\n  "shared.txt"\n]'
"""`prompt._inline_json`'s multi-line rendering of `["shared.txt"]`."""


def _git_ok(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _merge_head_exists(repo):
    return (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def _conflicted_repo(tmp_path, style="merge"):
    """A real repo stopped mid-merge: `main` says `ours`, `SIDE_BRANCH` says `theirs`.

    The conflict style is set in the repo's own config, so a developer's global
    `merge.conflictStyle` cannot change which markers the test sees.
    """
    repo = _implement_repo(tmp_path)
    _git_ok(repo, "config", "merge.conflictStyle", style)
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    _git_ok(repo, "add", "shared.txt")
    _git_ok(repo, "commit", "-m", "base line")
    _git_ok(repo, "checkout", "-b", SIDE_BRANCH)
    (repo / "shared.txt").write_text("theirs\n", encoding="utf-8")
    _git_ok(repo, "commit", "-am", "theirs")
    _git_ok(repo, "checkout", "main")
    (repo / "shared.txt").write_text("ours\n", encoding="utf-8")
    _git_ok(repo, "commit", "-am", "ours")
    merge = subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "--no-edit", SIDE_BRANCH],
        capture_output=True,
        text=True,
    )
    assert merge.returncode != 0, merge.stdout  # non-vacuity: a real conflict
    assert _merge_head_exists(repo)
    return repo


def _resolve_brief_text(tip=SIDE_BRANCH, files=CONFLICT_FILES):
    """A `resolve` brief shaped like `builtin/integrate.yaml` renders it."""
    text = (
        "# Resolver\n\nstanding instructions\n\n"
        "# phase: resolve\n# role: resolver\n"
        "\n## branch\nm3-integrate\n"
        "\n## base_branch\nmain\n"
    )
    if tip is not None:
        text += f"\n## merge_tip\n{tip}\n"
    if files is not None:
        text += f"\n## conflict_files\n{files}\n"
    return text


def _resolve(repo, **brief):
    return fake_claude.build_result(
        "resolve",
        fake_claude.payload_from_schema(RESOLVE_SCHEMA),
        _resolve_brief_text(**brief),
        repo,
    )


def test_the_resolver_env_var_name_is_pinned():
    assert fake_claude.RESOLVER_ENV == "FAKE_CLAUDE_RESOLVER"
    assert fake_claude.RESOLVER_REFUSE == "refuse"


@pytest.mark.git
@pytest.mark.parametrize("style", ["merge", "diff3", "zdiff3"])
def test_the_resolver_keeps_both_sides_commits_and_the_real_gate_passes(
    tmp_path, monkeypatch, style
):
    monkeypatch.delenv(fake_claude.RESOLVER_ENV, raising=False)
    repo = _conflicted_repo(tmp_path, style)

    payload = _resolve(repo)

    assert payload == {"resolved": True, "summary": fake_claude.SUMMARY}
    assert (repo / "shared.txt").read_text(encoding="utf-8") == "ours\ntheirs\n"
    assert not _merge_head_exists(repo)
    assert _porcelain(repo) == ""
    parents = _git_ok(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()
    assert len(parents) == 3  # a real merge commit: itself plus two parents
    assert merge_completed_gate(payload, repo) is None


@pytest.mark.git
def test_an_empty_resolver_env_value_means_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv(fake_claude.RESOLVER_ENV, "")
    repo = _conflicted_repo(tmp_path)

    _resolve(repo)

    assert not _merge_head_exists(repo)


@pytest.mark.git
def test_a_refusing_resolver_claims_resolved_but_git_still_says_no(
    tmp_path, monkeypatch
):
    """Addendum I3: `resolved` is advisory. Refuse mode lies, and the production
    gate, which only asks git, blocks it."""
    monkeypatch.setenv(fake_claude.RESOLVER_ENV, fake_claude.RESOLVER_REFUSE)
    repo = _conflicted_repo(tmp_path)
    before = (repo / "shared.txt").read_bytes()
    head = _head(repo)

    payload = _resolve(repo)

    assert payload == {"resolved": True, "summary": fake_claude.REFUSE_SUMMARY}
    assert _merge_head_exists(repo)
    assert (repo / "shared.txt").read_bytes() == before
    assert b"<<<<<<< " in before
    assert _head(repo) == head
    verdict = merge_completed_gate(payload, repo)
    assert verdict is not None and "MERGE_HEAD" in verdict["detail"]


@pytest.mark.git
def test_an_unknown_resolver_env_value_fails_the_fake_and_touches_nothing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(fake_claude.RESOLVER_ENV, "refused")
    repo = _conflicted_repo(tmp_path)
    before = (repo / "shared.txt").read_bytes()

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _resolve(repo)

    assert fake_claude.RESOLVER_ENV in str(caught.value)
    assert "refused" in str(caught.value)
    assert _merge_head_exists(repo)
    assert (repo / "shared.txt").read_bytes() == before


@pytest.mark.git
@pytest.mark.parametrize(
    ("missing", "brief"),
    [("merge_tip", {"tip": None}), ("conflict_files", {"files": None})],
)
def test_a_resolve_brief_missing_a_section_fails_the_fake(
    tmp_path, monkeypatch, missing, brief
):
    monkeypatch.delenv(fake_claude.RESOLVER_ENV, raising=False)
    repo = _conflicted_repo(tmp_path)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _resolve(repo, **brief)

    assert missing in str(caught.value)
    assert _merge_head_exists(repo)


@pytest.mark.git
@pytest.mark.parametrize(
    "files", ["not json", '{"shared.txt": 1}', "[1]", '[""]', '"shared.txt"']
)
def test_a_malformed_conflict_files_section_fails_the_fake(
    tmp_path, monkeypatch, files
):
    monkeypatch.delenv(fake_claude.RESOLVER_ENV, raising=False)
    repo = _conflicted_repo(tmp_path)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _resolve(repo, files=files)

    assert "conflict_files" in str(caught.value)
    assert _merge_head_exists(repo)


@pytest.mark.git
def test_a_merge_tip_that_is_not_merge_head_fails_the_fake(tmp_path, monkeypatch):
    monkeypatch.delenv(fake_claude.RESOLVER_ENV, raising=False)
    repo = _conflicted_repo(tmp_path)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _resolve(repo, tip="main")

    assert "MERGE_HEAD" in str(caught.value)
    assert _merge_head_exists(repo)


@pytest.mark.git
def test_a_resolve_with_no_merge_in_progress_fails_the_fake(tmp_path, monkeypatch):
    monkeypatch.delenv(fake_claude.RESOLVER_ENV, raising=False)
    repo = _implement_repo(tmp_path)
    _git_ok(repo, "branch", SIDE_BRANCH)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _resolve(repo)

    assert "MERGE_HEAD" in str(caught.value)


@pytest.mark.git
def test_a_listed_conflict_file_that_is_not_there_fails_before_any_write(
    tmp_path, monkeypatch
):
    """Review focus: nothing is rewritten or committed if one path is wrong."""
    monkeypatch.delenv(fake_claude.RESOLVER_ENV, raising=False)
    repo = _conflicted_repo(tmp_path)
    before = (repo / "shared.txt").read_bytes()

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _resolve(repo, files='["shared.txt", "missing.txt"]')

    assert "missing.txt" in str(caught.value)
    assert (repo / "shared.txt").read_bytes() == before
    assert _merge_head_exists(repo)


def test_the_resolver_env_var_is_the_conftest_twin():
    """The fixture sets `FAKE_RESOLVER_ENV`; the script reads `RESOLVER_ENV`."""
    assert fake_claude.RESOLVER_ENV == _conftest_constant("FAKE_RESOLVER_ENV")


def _critic_text(phase):
    """A critic brief, as `builtin/task.yaml` renders one, without its contract."""
    return (
        "# Critic\n\nstanding instructions\n\n"
        f"# phase: {phase}\n# role: critic\n"
        "\n## spec_path\ndocs/superpowers/specs/x-00000001.md\n"
    )


def _critic(phase, cwd):
    return fake_claude.build_result(
        phase, fake_claude.payload_from_schema(CRITIC_SCHEMA), _critic_text(phase), cwd
    )


def _arm_critic_blocks(tmp_path, monkeypatch, table):
    """Write the budget file and point the env var at it, as the e2e tests do."""
    budget = tmp_path / "critic-blocks.json"
    budget.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setenv(fake_claude.CRITIC_BLOCKS_ENV, str(budget))
    return budget


def test_the_critic_blocks_names_are_pinned():
    """`tests/e2e/test_production_wiring.py` re-declares these as literals; the
    script and the tests meet across a process boundary, like `RESOLVER_ENV`."""
    assert fake_claude.CRITIC_BLOCKS_ENV == "FAKE_CLAUDE_CRITIC_BLOCKS"
    assert fake_claude.CRITIC_BLOCK_REASON == (
        "fake-claude critic: the critic-blocks budget told this critic to block"
    )
    assert fake_claude.CRITIC_PHASES == ("validate_spec", "validate_plan")
    assert fake_claude.MAX_CRITIC_BLOCKS == 2


@pytest.mark.parametrize("value", [None, ""])
@pytest.mark.parametrize("phase", ["validate_spec", "validate_plan"])
def test_without_a_critic_blocks_budget_every_critic_passes(
    tmp_path, monkeypatch, phase, value
):
    """Unset or empty is today's behaviour exactly."""
    if value is None:
        monkeypatch.delenv(fake_claude.CRITIC_BLOCKS_ENV, raising=False)
    else:
        monkeypatch.setenv(fake_claude.CRITIC_BLOCKS_ENV, value)

    payload = _critic(phase, tmp_path)

    assert payload == {"blockers": False, "reason": None, "summary": fake_claude.SUMMARY}
    assert critic_blockers_gate(payload) is None


def test_a_budget_of_one_blocks_once_with_the_fixed_reason_then_passes(
    tmp_path, monkeypatch
):
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {"validate_spec": 1})

    first = _critic("validate_spec", tmp_path)

    assert first == {
        "blockers": True,
        "reason": fake_claude.CRITIC_BLOCK_REASON,
        "summary": fake_claude.SUMMARY,
    }
    # The production gate turns it into a `validation` block carrying the reason.
    assert critic_blockers_gate(first) == {
        "blocked": "validation",
        "detail": fake_claude.CRITIC_BLOCK_REASON,
    }
    assert json.loads(budget.read_text(encoding="utf-8")) == {"validate_spec": 0}

    second = _critic("validate_spec", tmp_path)

    assert second["blockers"] is False
    assert second["reason"] is None


def test_a_budget_of_two_blocks_twice_then_passes(tmp_path, monkeypatch):
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {"validate_plan": 2})

    verdicts = [_critic("validate_plan", tmp_path)["blockers"] for _ in range(3)]

    assert verdicts == [True, True, False]
    assert json.loads(budget.read_text(encoding="utf-8")) == {"validate_plan": 0}


def test_a_budget_for_one_critic_leaves_the_other_passing_and_the_file_untouched(
    tmp_path, monkeypatch
):
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {"validate_plan": 1})
    before = budget.read_text(encoding="utf-8")

    payload = _critic("validate_spec", tmp_path)

    assert payload["blockers"] is False
    assert budget.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "[]",
        '"validate_spec"',
        '{"review": 1}',
        '{"validate_spec": 3}',
        '{"validate_spec": -1}',
        '{"validate_spec": "1"}',
        '{"validate_spec": true}',
        '{"validate_spec": 1.0}',
        '{"validate_spec": 1, "spec": 1}',
    ],
)
def test_a_malformed_critic_blocks_budget_stops_the_fake(tmp_path, monkeypatch, raw):
    """Review focus 4: a typo in a test must never read as "no block"."""
    budget = tmp_path / "critic-blocks.json"
    budget.write_text(raw, encoding="utf-8")
    monkeypatch.setenv(fake_claude.CRITIC_BLOCKS_ENV, str(budget))

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _critic("validate_spec", tmp_path)

    assert fake_claude.CRITIC_BLOCKS_ENV in str(caught.value)
    assert budget.read_text(encoding="utf-8") == raw


def test_a_critic_blocks_env_naming_a_missing_file_stops_the_fake(tmp_path, monkeypatch):
    missing = tmp_path / "nowhere.json"
    monkeypatch.setenv(fake_claude.CRITIC_BLOCKS_ENV, str(missing))

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _critic("validate_spec", tmp_path)

    assert fake_claude.CRITIC_BLOCKS_ENV in str(caught.value)
    assert str(missing) in str(caught.value)


@pytest.mark.e2e_fake
def test_a_bad_critic_blocks_budget_makes_the_fake_process_exit_1(tmp_path, monkeypatch):
    """The `__main__` mapping, end to end: the child inherits the env (as it does
    under `launcher.run_direct`), refuses the budget, writes no result."""
    budget = tmp_path / "critic-blocks.json"
    budget.write_text('{"validate_spec": 9}', encoding="utf-8")
    monkeypatch.setenv(fake_claude.CRITIC_BLOCKS_ENV, str(budget))
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.1"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path, "validate_spec", "spec_critic", "\n## spec_path\nx.md\n",
        CRITIC_SCHEMA, result_path,
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 1
    assert "FAKE_CLAUDE_CRITIC_BLOCKS" in completed.stderr
    assert not result_path.exists()


@pytest.mark.e2e_fake
def test_a_blocking_critic_process_writes_the_blocked_result(tmp_path, monkeypatch):
    """The whole script, driven by a brief plus the env switch: the brief says
    nothing about blocking, the budget alone decides (Rule 4)."""
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {"validate_spec": 1})
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.1"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path, "validate_spec", "spec_critic", "\n## spec_path\nx.md\n",
        CRITIC_SCHEMA, result_path,
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["blockers"] is True
    assert payload["reason"] == fake_claude.CRITIC_BLOCK_REASON
    assert json.loads(budget.read_text(encoding="utf-8")) == {"validate_spec": 0}
