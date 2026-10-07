"""Where `agent_manager.store.replay`'s names live and what the module may
import: folding journal lines back into a run's §9 tree, and comparing that
tree with the projection.

Replay and divergence behaviour is tested in `tests/test_store.py`, whose
tests build their inputs through `Store`. Everything here imports modules or
reads source files; nothing spawns a process, so these are unit tests.
"""

import ast
import dataclasses
import inspect
import re
import sys
from pathlib import Path
from typing import get_args

from agent_manager import store
from agent_manager.store import journal as store_journal
from agent_manager.store import replay as store_replay

_REPO = Path(__file__).resolve().parents[2]

_REPLAY_NAMES = (
    "diverging",
    "Mismatch",
    "MismatchKind",
    "ProjectionDivergedError",
    "_describe_node",
    "_RETIRED_ATTEMPT_KEYS",
    "_current_attempt_payload",
    "_upsert",
    "_find",
    "_NodeKey",
    "_RUN_KEY",
    "_NODE_MODELS",
    "_LEVELS",
    "_coords",
    "_child_key",
    "_line_node",
    "_journaled_statuses",
    "_walk",
)


def test_replay_is_a_leaf_module_of_the_store_package():
    for obj in (
        store_replay.replay,
        store_replay.diverging,
        store_replay.Mismatch,
        store_replay.ProjectionDivergedError,
    ):
        assert obj.__module__ == "agent_manager.store.replay"
    assert issubclass(store_replay.ProjectionDivergedError, RuntimeError)
    assert not issubclass(store_replay.ProjectionDivergedError, store_journal.JournalError)
    assert set(get_args(store_replay.MismatchKind)) == {"stale", "foreign"}
    # `am status` emits `dataclasses.asdict(mismatch)`: the field order is its shape.
    assert [field.name for field in dataclasses.fields(store_replay.Mismatch)] == [
        "node",
        "field",
        "journal",
        "projection",
        "kind",
    ]


def test_the_store_package_does_not_re_export_replay_names():
    assert [name for name in _REPLAY_NAMES if hasattr(store, name)] == []
    # Importing the submodule binds it as the package's `replay` attribute, so a
    # leftover `store.replay(lines)` is a call on a module, not the function.
    assert inspect.ismodule(store.replay)
    assert store.replay is store_replay


def test_replay_imports_only_the_stdlib_pydantic_models_and_the_journal():
    # The AST, not `sys.modules`: importing `agent_manager.store.replay` always
    # runs the package `__init__` first, so `sys.modules` cannot tell them apart.
    tree = ast.parse(Path(store_replay.__file__).read_text())
    outside: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in sys.stdlib_module_names:
                    outside.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                outside.append("." * node.level + module)
            elif module == "agent_manager":
                outside.extend(
                    f"agent_manager.{alias.name}"
                    for alias in node.names
                    if alias.name != "models"
                )
            elif module == "agent_manager.store":
                outside.extend(
                    f"agent_manager.store.{alias.name}"
                    for alias in node.names
                    if alias.name != "journal"
                )
            elif module.split(".")[0] not in (*sys.stdlib_module_names, "pydantic"):
                outside.append(module)
    assert outside == []


_THROUGH_THE_PACKAGE = re.compile(
    r"\bstore(_module)?\.(diverging|Mismatch|MismatchKind|ProjectionDivergedError"
    r"|_describe_node|_RETIRED_ATTEMPT_KEYS|_current_attempt_payload|_journaled_statuses"
    r"|_walk)\b"
    r"|\bstore(_module)?\.replay\("
    r"|from agent_manager\.store import (?!replay\b).*\b(diverging|Mismatch|MismatchKind"
    r"|ProjectionDivergedError)\b"
)


def test_no_caller_reaches_a_replay_name_through_the_store_package():
    # The opt-in tiers (e2e_fake, soak, e2e) never run in the default suite,
    # and `--collect-only` does not run test bodies, so a stale call through
    # the package there would only fail when someone runs that tier.
    # `store.replay` without a call is allowed: it names the module now.
    assert _THROUGH_THE_PACKAGE.search("found = store_module.diverging(lines, run)")
    assert _THROUGH_THE_PACKAGE.search("run = store.replay(lines)")
    assert _THROUGH_THE_PACKAGE.search("with pytest.raises(store.ProjectionDivergedError):")
    assert _THROUGH_THE_PACKAGE.search("from agent_manager.store import Mismatch, Store")
    assert not _THROUGH_THE_PACKAGE.search("found = store_replay.diverging(lines, run)")
    assert not _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store import replay as store_replay"
    )
    assert not _THROUGH_THE_PACKAGE.search("from agent_manager.store.replay import Mismatch")
    assert not _THROUGH_THE_PACKAGE.search("# reconciled in `store.replay`, never")
    me = Path(__file__).resolve()
    hits = [
        f"{path.relative_to(_REPO)}:{number}: {line.strip()}"
        for root in (_REPO / "src", _REPO / "tests")
        for path in sorted(root.rglob("*.py"))
        if path.resolve() != me and "__pycache__" not in path.parts
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if _THROUGH_THE_PACKAGE.search(line)
    ]
    assert hits == []


def test_status_integrity_calls_diverging_through_the_replay_module():
    # `test_cli`'s live-lease test patches `store_replay.diverging`. A `cli`
    # that bound `diverging` by name would never see the patch, and that test
    # would still pass.
    tree = ast.parse((_REPO / "src" / "agent_manager" / "cli.py").read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "diverging"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "store_replay"
    ]
    bound = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "agent_manager.store.replay"
        for alias in node.names
    ]
    assert calls != []
    assert "diverging" not in bound
