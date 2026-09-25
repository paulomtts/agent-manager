"""Tests for the fake `claude` of the production-wiring tier.

The parsing and payload-generation tests are pure-functions tier (design §14
lines 477-492); the four `_run_fake` tests below drive the whole script as a
child process and belong to the production-wiring tier, like the fixture they
underwrite.

`tests/e2e/fake_claude.py` is a script, not a package module: it is copied to a
tmp directory and executed as `claude` by the production-wiring tier. It is
loaded here by path rather than imported by name, because `tests/e2e` is not on
`sys.path` under `--import-mode=importlib`.
"""

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_manager.steps.reducers import review_gate

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


def test_the_fake_writes_a_gate_passing_critic_result_where_the_brief_says(tmp_path):
    """Production-wiring tier's own fixture check: the script end to end, driven
    only by a brief on disk."""
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.1"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path,
        "validate_spec",
        "critic",
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


def test_the_fake_logs_its_phase_and_cwd_beside_the_run_directory(tmp_path):
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.1"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path, "validate_spec", "critic", "\n## spec_path\nx.md\n",
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


def test_a_brief_without_a_result_contract_makes_the_fake_exit_non_zero(tmp_path):
    """Review focus / addendum R2: no contract, no run. This is what makes the
    production-wiring test fail loudly if prompt composition ever regresses."""
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text(
        "# Critic\n\n# phase: validate_spec\n# role: critic\n\n## spec_path\nx.md\n",
        encoding="utf-8",
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 1
    assert "Result contract" in completed.stderr


def test_a_feedback_block_after_the_contract_does_not_hide_the_contract(tmp_path):
    """Review focus: `dispatch._append_feedback` appends
    `## feedback on the previous attempt` AFTER the contract on a retry."""
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.2"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path, "validate_spec", "critic", "\n## spec_path\nx.md\n",
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
    )
    return fake_claude.build_result(
        "review", fake_claude.payload_from_schema(REVIEW_SCHEMA), text, worktree
    )


def test_the_review_fail_marker_name_is_the_one_the_e2e_fixtures_write():
    """`tests/e2e/conftest.py` writes `FAKE_REVIEW_FAIL_MARKER`; the script and the
    fixture meet across a process boundary, like `LOG_NAME`."""
    assert fake_claude.REVIEW_FAIL_MARKER == "fake-claude-review-fail"


def test_without_a_marker_the_review_passes_the_real_review_gate(tmp_path):
    _, worktree = _review_worktree(tmp_path)

    payload = _review(worktree)

    assert payload["findings"] == []
    assert payload["unresolved_blockers"] == []
    assert payload["porcelain"] == ""
    assert payload["commit_count"] == payload["tagged_count"] == 1
    assert review_gate(payload, REVIEW_BRANCH, "main") is None


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


def test_a_marker_entry_for_another_branch_writes_nothing_extra(tmp_path):
    repo = _implement_repo(tmp_path)
    _write_edits(repo, {OTHER_BRANCH: {"shared.txt": "story B\n"}})

    payload = _implement_on_branch(repo)

    assert payload["resumed"] is False
    assert not (repo / "shared.txt").exists()
    assert _porcelain(repo) == ""


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


def test_a_marker_that_is_not_json_is_refused(tmp_path):
    repo = _implement_repo(tmp_path)
    (repo / ".git" / fake_claude.IMPLEMENT_EDITS_MARKER).write_text(
        "{not json", encoding="utf-8"
    )

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _implement_on_branch(repo)

    assert "not valid JSON" in str(caught.value)


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
