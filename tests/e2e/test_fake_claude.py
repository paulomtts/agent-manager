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

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SOURCE = Path(__file__).with_name("fake_claude.py")
_spec = importlib.util.spec_from_file_location("e2e_fake_claude", _SOURCE)
assert _spec is not None and _spec.loader is not None
fake_claude = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_claude)


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


def _implement_brief_text(digest=None):
    """An `implement` brief, optionally without its `## plan_hash` section."""
    section = "" if digest is None else f"\n## plan_hash\n{digest}\n"
    return (
        "# Coder\n\nstanding instructions\n\n"
        "# phase: implement\n# role: coder\n"
        f"\n## plan_path\n{PLAN_RELATIVE}\n"
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
