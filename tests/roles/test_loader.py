"""Behaviour of the role bundle loader (design §8 lines 320-334, card 47bd4ee6).

Placement follows design §14: `roles/loader.py` is deterministic parse-and-
validate over files on disk -- no git repository, no `brd` board, no harness,
no network -- so it sits in the Pure-functions/unit tier alongside
`tests/test_models.py`, not in the Steps tier (`tests/steps/`, temp git repos)
and not in the Adapters tier (`harness/*.py`, a sibling card).

Synthetic bundles are built in `tmp_path` for every error path; the six shipped
bundles are exercised through the same public API, because §8 makes the bundles
themselves part of the contract.
"""

import hashlib
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_manager.roles import loader
from agent_manager.roles.loader import RoleBundleError

POLICY = """\
allowed_tools = ["Read", "Grep"]
max_attempts = 2
required_capabilities = []

[default_model]
claude = "sonnet"
"""


def lock_entry(
    filename: str,
    text: str,
    *,
    upstream: str = "superpowers/6.4.1/skills/writing-plans/SKILL.md",
    sha256: str | None = None,
) -> str:
    """One `[[vendored]]` table, hashing `text` unless a hash is forced."""
    digest = sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest()
    return (
        "[[vendored]]\n"
        f'file = "{filename}"\n'
        f'upstream = "{upstream}"\n'
        f'sha256 = "{digest}"\n'
    )


def make_bundle(
    root: Path,
    name: str = "coder",
    *,
    system: str | None = "Standing instructions for the coder role.\n",
    policy: str | None = POLICY,
    lock: str | None = "vendored = []\n",
    methodology: dict[str, str] | None = None,
) -> Path:
    """Build a synthetic bundle under `root`, returning its directory.

    `None` for `system`, `policy` or `lock` omits that file entirely.
    """
    directory = root / name
    (directory / "methodology").mkdir(parents=True, exist_ok=True)
    if system is not None:
        (directory / "system.md").write_text(system, encoding="utf-8")
    if policy is not None:
        (directory / "policy.toml").write_text(policy, encoding="utf-8")
    if lock is not None:
        (directory / "VENDORED.lock").write_text(lock, encoding="utf-8")
    for filename, text in (methodology or {}).items():
        (directory / "methodology" / filename).write_text(text, encoding="utf-8")
    return directory


def test_loading_a_bundle_returns_its_name_and_verbatim_system_text(tmp_path):
    system = "# Coder\n\nWrite the failing test first.\n\n\ttrailing tab line\n"
    directory = make_bundle(tmp_path, system=system)

    bundle = loader.load_role("coder", root=tmp_path)

    assert bundle.name == "coder"
    assert bundle.system == system
    assert bundle.system == (directory / "system.md").read_text(encoding="utf-8")


def test_loading_a_bundle_exposes_every_policy_field(tmp_path):
    make_bundle(
        tmp_path,
        policy=(
            'allowed_tools = ["Read", "Edit", "Bash"]\n'
            "max_attempts = 3\n"
            'required_capabilities = ["browser"]\n'
            "\n"
            "[default_model]\n"
            'claude = "sonnet"\n'
            'codex = "gpt-5"\n'
        ),
    )

    policy = loader.load_role("coder", root=tmp_path).policy

    assert policy.allowed_tools == ["Read", "Edit", "Bash"]
    assert policy.max_attempts == 3
    assert policy.required_capabilities == ["browser"]
    assert policy.default_model == {"claude": "sonnet", "codex": "gpt-5"}


def test_required_capabilities_defaults_to_empty(tmp_path):
    make_bundle(
        tmp_path,
        policy=(
            'allowed_tools = ["Read"]\n'
            "max_attempts = 1\n"
            "\n"
            "[default_model]\n"
            'claude = "opus"\n'
        ),
    )

    assert loader.load_role("coder", root=tmp_path).policy.required_capabilities == []


def test_methodology_documents_are_keyed_by_filename_with_exact_text(tmp_path):
    tdd = "# TDD\n\nRED, GREEN, REFACTOR.\n"
    plans = "# Writing plans\n\nBite-sized steps.\n"
    make_bundle(
        tmp_path,
        lock=lock_entry("test-driven-development.md", tdd)
        + "\n"
        + lock_entry("writing-plans.md", plans),
        methodology={"test-driven-development.md": tdd, "writing-plans.md": plans},
    )

    methodology = loader.load_role("coder", root=tmp_path).methodology

    assert methodology == {
        "test-driven-development.md": tdd,
        "writing-plans.md": plans,
    }


def test_two_loads_of_the_same_role_are_equal(tmp_path):
    make_bundle(tmp_path)

    assert loader.load_role("coder", root=tmp_path) == loader.load_role(
        "coder", root=tmp_path
    )


def test_a_loaded_bundle_is_immutable(tmp_path):
    make_bundle(tmp_path)
    bundle = loader.load_role("coder", root=tmp_path)

    with pytest.raises(ValidationError):
        bundle.system = "tampered"


def test_list_roles_returns_sorted_directory_names(tmp_path):
    make_bundle(tmp_path, "reviewer")
    make_bundle(tmp_path, "coder")
    make_bundle(tmp_path, "explorer")

    assert loader.list_roles(tmp_path) == ["coder", "explorer", "reviewer"]


def test_list_roles_ignores_private_and_dotted_directories(tmp_path):
    make_bundle(tmp_path, "coder")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".ipynb_checkpoints").mkdir()
    (tmp_path / "README.md").write_text("not a bundle\n", encoding="utf-8")

    assert loader.list_roles(tmp_path) == ["coder"]


def test_bundles_dir_points_inside_the_installed_package():
    directory = loader.bundles_dir()

    assert directory.name == "bundles"
    assert directory.parent.name == "roles"
