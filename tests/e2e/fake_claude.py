"""A fake `claude` executable: the model stand-in of the production-wiring tier.

Test infrastructure, not product code. It is copied to a tmp directory as an
executable named `claude` and put first on `PATH`, so `harness/claude.py`'s bare
`COMMAND = "claude"` resolves to it and the real adapter, the real
`launcher.run_direct` and a real child process all run unmodified.

Everything it needs comes out of the brief on disk: the prompt path from the
adapter's `-p` sentence (`harness/claude.py:30`), and the absolute result path
plus the JSON Schema from the `## Result contract` section the brief carries
(`prompt.py:281-374`). There is deliberately no environment variable, no extra
argv flag and no import of `agent_manager` -- a brief that omits the contract
must make this script fail, because that failure is the test's whole point.

Standard library only: it runs under a bare `#!<python>` line.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


class FakeClaudeError(RuntimeError):
    """The fake cannot do its job from the brief it was given."""


PROMPT_SENTENCE = re.compile(
    r"^Read (?P<path>.+?) and follow the instructions in it exactly\."
)
"""The adapter's `-p` text (`harness/claude.py:30`), read backwards."""

PHASE_HEADER = re.compile(r"^# phase: (?P<phase>\S+)\n# role: (?P<role>\S+)$", re.M)
"""`prompt._assemble`'s two-line head (`prompt.py:267-269`)."""


def prompt_path_from_argv(argv):
    """The brief's path, out of the adapter's `-p` sentence."""
    if "-p" not in argv:
        raise FakeClaudeError(f"no -p argument in argv: {argv!r}")
    sentence = argv[argv.index("-p") + 1]
    found = PROMPT_SENTENCE.match(sentence)
    if found is None:
        raise FakeClaudeError(
            f"the -p text is not the adapter's instruction sentence: {sentence!r}"
        )
    return Path(found.group("path"))


def phase_of(text):
    """The phase name the rendered prompt's header states."""
    found = PHASE_HEADER.search(text)
    if found is None:
        raise FakeClaudeError("the brief carries no `# phase:`/`# role:` header")
    return found.group("phase")


RESULT_HEADING = "## Result contract"
"""`prompt.RESULT_HEADING` (`prompt.py:281`), matched as a literal."""

RESULT_PATH_LEAD = "write your result as valid JSON to exactly this path:"
"""The sentence `prompt._result_contract` puts immediately before the path."""

SCHEMA_FENCE = re.compile(r"```json\n(?P<schema>.*?)\n```", re.S)

SECTION = re.compile(r"^## (?P<name>.+)$", re.M)
"""Any `## ` heading. Names are taken verbatim and matched case-sensitively:
the `repo_docs` body inlines a repo CLAUDE.md that may carry its own
`## Verification` heading, and that must not be read as the `verification`
input."""


def _contract(text):
    start = text.find(RESULT_HEADING)
    if start < 0:
        raise FakeClaudeError(
            f"the brief has no {RESULT_HEADING!r} section, so it names no result "
            "path and no schema; this fake has no other way to learn either "
            "(addendum R2)"
        )
    return text[start:]


def result_path_of(text):
    """The absolute result path the contract names."""
    contract = _contract(text)
    lead = contract.find(RESULT_PATH_LEAD)
    if lead < 0:
        raise FakeClaudeError(
            f"the {RESULT_HEADING!r} section does not say {RESULT_PATH_LEAD!r}"
        )
    for line in contract[lead + len(RESULT_PATH_LEAD) :].splitlines():
        if line.strip():
            return Path(line.strip())
    raise FakeClaudeError("the result contract names no path after its lead line")


def schema_of(text):
    """The JSON Schema embedded in the contract, as a dict."""
    found = SCHEMA_FENCE.search(_contract(text))
    if found is None:
        raise FakeClaudeError("the result contract embeds no ```json schema fence")
    return json.loads(found.group("schema"))


def sections(text):
    """Every `## <name>` section below the `# phase:` header, first wins."""
    head = PHASE_HEADER.search(text)
    body = text[head.end() :] if head is not None else text
    marks = list(SECTION.finditer(body))
    found = {}
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        found.setdefault(mark.group("name"), body[mark.end() : end].strip("\n"))
    return found


def payload_from_schema(schema, defs=None):
    """A schema-shaped payload: every declared property at its type's zero value.

    Generated rather than hardcoded, so this fake cannot drift away from the
    models the run validates against. Values that a *gate* needs (a real
    summary, a real commit count) are set afterwards by `override`.
    """
    table = schema.get("$defs", {}) if defs is None else defs
    return {
        name: _value_for(node, table)
        for name, node in schema.get("properties", {}).items()
    }


def _value_for(node, defs):
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        if name not in defs:
            raise FakeClaudeError(f"the schema references unknown $def {name!r}")
        return payload_from_schema(defs[name], defs)
    if "anyOf" in node:
        options = node["anyOf"]
        if any(option.get("type") == "null" for option in options):
            return None
        return _value_for(options[0], defs)
    kind = node.get("type")
    if kind == "object":
        return payload_from_schema(node, defs)
    if kind == "array":
        return []
    if kind == "string":
        return ""
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return False
    raise FakeClaudeError(f"no default value for schema node {node!r}")


def override(payload, **fields):
    """Set each named field, refusing a name the schema did not produce.

    The refusal is the drift detector: if a result model renames a field, this
    fake stops instead of writing a well-typed payload that every gate then
    reads as `None`.
    """
    for name, value in fields.items():
        if name not in payload:
            raise FakeClaudeError(
                f"the embedded schema has no field {name!r} "
                f"(it has: {sorted(payload)})"
            )
        payload[name] = value
    return payload
