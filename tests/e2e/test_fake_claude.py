"""Pure-functions tier (design §14 lines 477-492) for the fake `claude`'s helpers.

`tests/e2e/fake_claude.py` is a script, not a package module: it is copied to a
tmp directory and executed as `claude` by the production-wiring tier. It is
loaded here by path rather than imported by name, because `tests/e2e` is not on
`sys.path` under `--import-mode=importlib`.
"""

import importlib.util
import json
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
