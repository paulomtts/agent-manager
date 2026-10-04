"""Behaviour of the Claude harness adapter (design §8 lines 304-318, card
9a2524a8).

Unit tier per design §14 line 484 ("Adapters -- `build_command` is pure and
asserted per harness; the launcher is injected, so no harness is executed in
unit tests"). Nothing here spawns a process, runs a real `claude` binary,
creates a worktree or writes a result file: `build_command` is asserted on the
argv it returns. Launcher behaviour is `test_launcher.py`'s; canned result
files belong to the engine card (§14 line 486); a real harness run is the
single opt-in end-to-end test (§14 lines 489-490).
"""

import ast
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

from agent_manager.harness import claude as claude_module
from agent_manager.harness.base import HarnessAdapter
from agent_manager.harness.claude import ClaudeAdapter
from agent_manager.models import Dispatch
from agent_manager.roles.loader import bundles_dir


def _dispatch(**overrides: object) -> Dispatch:
    """A representative dispatch: absolute paths, a real role, a real model."""
    fields: dict[str, object] = {
        "harness": "claude",
        "model": "sonnet",
        "role": "coder",
        "cwd": Path("/repo/wt/9a2524a8"),
        "prompt_path": Path("/runs/run-1/9a2524a8/implement.1/prompt.md"),
        "result_path": Path("/runs/run-1/9a2524a8/implement.1/result.json"),
        "timeout": 60.0,
    }
    fields.update(overrides)
    return Dispatch(**fields)


def test_the_adapter_satisfies_the_protocol_structurally():
    # `HarnessAdapter` is deliberately not runtime_checkable (see its
    # docstring in base.py), so conformance is asserted member by member --
    # and the adapter inherits from nothing, which is the whole point of a
    # structural Protocol.
    adapter = ClaudeAdapter()
    for member in HarnessAdapter.__protocol_attrs__:
        assert hasattr(adapter, member), member
    assert ClaudeAdapter.__bases__ == (object,)
    assert callable(adapter.build_command)


def test_the_name_is_the_one_key_routing_and_policy_both_use():
    # `Dispatch.harness`, `Policy.default_model`'s key and the journalled
    # harness name are one spelling; a rename here would silently strand
    # `default_model.claude` in every bundle.
    assert ClaudeAdapter.name == "claude"
    policy_path = bundles_dir() / "coder" / "policy.toml"
    policy = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    assert ClaudeAdapter.name in policy["default_model"]
    assert policy["default_model"][ClaudeAdapter.name] == "sonnet"


def test_capabilities_is_a_frozenset_of_non_empty_strings():
    # Data for the engine's plan-time capability check (§8 lines 336-339).
    # Frozen because it is class-level shared state, and a phase's `needs:`
    # check must not be able to mutate what a harness claims.
    assert isinstance(ClaudeAdapter.capabilities, frozenset)
    assert ClaudeAdapter.capabilities
    for capability in ClaudeAdapter.capabilities:
        assert isinstance(capability, str) and capability.strip()
    # What it claims is the engine's routing decision, so the set itself is
    # pinned. `browser` stays out: Claude Code only drives one through an
    # extension a headless runner need not have, and claiming it would let the
    # capability check pass for a phase that then cannot run. Claiming less
    # than the truth only refuses a phase early, which is the safe direction.
    assert ClaudeAdapter.capabilities == frozenset({"bash", "edit"})


EXPECTED_PROMPT_ARGUMENT = (
    "Read /runs/run-1/9a2524a8/implement.1/prompt.md and follow the "
    "instructions in it exactly. It is your complete brief for this task."
)


def test_build_command_returns_the_whole_expected_argv():
    # Exact equality, so the print-mode flag, the model, the permission bypass
    # and the prompt-path reference are each pinned: this argv is what actually
    # runs against a real worktree.
    assert ClaudeAdapter().build_command(_dispatch()) == [
        "claude",
        "--model",
        "sonnet",
        "--dangerously-skip-permissions",
        "-p",
        EXPECTED_PROMPT_ARGUMENT,
    ]


def test_the_argv_is_a_list_of_plain_strings():
    # §5 line 252: an argv list, never a shell string, and never `Path`
    # objects -- the launcher hands this straight to subprocess without a
    # shell.
    argv = ClaudeAdapter().build_command(_dispatch())
    assert isinstance(argv, list)
    for word in argv:
        assert type(word) is str


def test_the_model_is_passed_through_verbatim():
    # The adapter picks no default and rewrites no model name; defaulting is
    # the role policy's job upstream, so an unknown alias must survive intact.
    argv = ClaudeAdapter().build_command(_dispatch(model="a-model-that-ships-in-2030"))
    assert argv[argv.index("--model") + 1] == "a-model-that-ships-in-2030"


def test_the_prompt_is_referenced_by_absolute_path_and_never_inlined(tmp_path):
    # A real file with known contents: if the adapter ever read it, the
    # contents would show up in the argv. Referencing beats inlining (§7 lines
    # 296-298) and is what keeps build_command pure.
    prompt = tmp_path / "prompt.md"
    prompt.write_text("SECRET-PROMPT-BODY\n", encoding="utf-8")
    argv = ClaudeAdapter().build_command(_dispatch(prompt_path=prompt))
    assert str(prompt) in argv[-1]
    assert "SECRET-PROMPT-BODY" not in " ".join(argv)
    # That the reference is *absolute* is not asserted here: `tmp_path` is
    # always absolute, so the assertion would hold whatever the adapter did.
    # `test_build_command_refuses_a_relative_path` is where that is pinned.


def test_the_argv_carries_no_cwd_and_no_timeout():
    # D7's "cwd pinned to the worktree" is the launcher's `cwd=d.cwd`, and the
    # timeout is its kill deadline. A flag here would be a second source of
    # truth that can disagree with the directory the process starts in.
    d = _dispatch()
    argv = ClaudeAdapter().build_command(d)
    joined = " ".join(argv)
    for forbidden in ("--cwd", "--add-dir", "--directory", "-C", "--timeout"):
        assert forbidden not in argv
    assert str(d.cwd) not in joined
    assert str(d.result_path) not in joined
    assert "60" not in joined


def test_build_command_is_pure():
    # Same dispatch in, same argv out, and the dispatch comes back untouched:
    # the engine reuses one Dispatch across a retry.
    d = _dispatch()
    before = d.model_dump()
    first = ClaudeAdapter().build_command(d)
    second = ClaudeAdapter().build_command(d)
    assert first == second
    assert d.model_dump() == before


def test_build_command_refuses_a_dispatch_for_another_harness():
    with pytest.raises(ValueError, match="codex"):
        ClaudeAdapter().build_command(_dispatch(harness="codex"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cwd", Path("wt/9a2524a8")),
        ("prompt_path", Path("prompt.md")),
        ("result_path", Path("result.json")),
    ],
)
def test_build_command_refuses_a_relative_path(field, value):
    # A relative result path lands the result file inside the worktree and gets
    # it committed, which is exactly what D4 keeps outside.
    with pytest.raises(ValueError, match=field):
        ClaudeAdapter().build_command(_dispatch(**{field: value}))


def test_a_prompt_path_with_spaces_and_quotes_is_not_escaped():
    # The launcher runs without a shell, so any quoting the adapter added
    # would become part of the filename the harness tries to open.
    weird = Path("/runs/run 1/o'brien's plan/prompt.md")
    argv = ClaudeAdapter().build_command(_dispatch(prompt_path=weird))
    assert str(weird) in argv[-1]
    assert "\\" not in argv[-1]
    assert '"' not in argv[-1]


def test_a_model_that_looks_like_a_flag_stays_its_own_argv_element():
    # A pasted flag or a typo in `harness_map` must not merge into, or drop
    # out of, the argv -- argv elements are never re-split.
    argv = ClaudeAdapter().build_command(_dispatch(model="--help"))
    assert argv[:3] == ["claude", "--model", "--help"]


def _imported_names(source: str) -> set[str]:
    """Every module a source file imports, in every spelling it could use.

    `import os.path`, `from os import execv` and `from agent_manager.harness
    import launcher` all reach the same modules as the obvious spellings, so
    every dotted prefix and every `from X import y` pair is reported. A guard
    that only knew the obvious spelling would pass on the very rewrite it
    exists to catch.
    """
    names: set[str] = set()

    def add(dotted: str) -> None:
        parts = dotted.split(".")
        names.update(".".join(parts[: i + 1]) for i in range(len(parts)))

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            add(node.module)
            for alias in node.names:
                add(f"{node.module}.{alias.name}")
    return names


FORBIDDEN_IMPORTS = frozenset(
    {"subprocess", "os", "shutil", "signal", "agent_manager.harness.launcher"}
)
"""Everything that could start, find or signal a process from inside the
adapter. `agent_manager.harness.launcher` is listed because reaching the
launcher directly defeats §14 line 485's injection just as thoroughly as
`subprocess` would."""


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess",
        "import os",
        "import os.path",
        "from os import execv",
        "import subprocess as sp",
        "from agent_manager.harness import launcher",
        "from agent_manager.harness.launcher import run_direct",
        "import agent_manager.harness.launcher as runner",
        "import shutil",
        "import signal",
    ],
)
def test_the_import_guard_catches_every_spelling_of_launching(source):
    # The guard below is only worth its line count if it fails on the rewrites
    # a future edit would actually reach for, so each one is pinned here.
    assert _imported_names(source) & FORBIDDEN_IMPORTS, source


@pytest.mark.parametrize(
    "source",
    ["import math", "import re", "from pydantic import ValidationError", ""],
)
def test_the_import_guard_clears_what_the_adapter_legitimately_needs(source):
    # And it must not be a guard that rejects everything, which would pass the
    # test above for the wrong reason.
    assert not _imported_names(source) & FORBIDDEN_IMPORTS, source


def test_the_adapter_launches_nothing_itself():
    # §14 line 485: the launcher is injected, and one launcher serves every
    # adapter. The moment this module can start a process, that stops being
    # true and every test above it starts spawning things.
    source = Path(claude_module.__file__).read_text(encoding="utf-8")
    assert not _imported_names(source) & FORBIDDEN_IMPORTS
    # Imports are the source-level half; these are the runtime half, catching a
    # module pulled in under any alias by `importlib` or assigned after import.
    for name, value in vars(claude_module).items():
        assert not isinstance(value, ModuleType) or value.__name__ in {
            "math",
            "re",
        }, name
    assert not hasattr(claude_module, "run_direct")


def test_the_adapter_imports_neither_re_nor_math_nor_pydantic():
    # Parent spec 2026-10-03 §5.3 item 7. The adapter reads nothing back from
    # the harness (D4), so the old log scanner's machinery has no reason to
    # be imported here; this pins its removal so `parse_usage` cannot quietly
    # come back. Distinct from FORBIDDEN_IMPORTS, which guards launching.
    source = Path(claude_module.__file__).read_text(encoding="utf-8")
    assert _imported_names(source) & {"re", "math", "pydantic"} == set()
