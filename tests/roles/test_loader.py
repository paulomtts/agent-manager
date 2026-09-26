"""Behaviour of the role bundle loader (design §8 lines 320-334, card 47bd4ee6).

Placement follows design §14: `roles/loader.py` is deterministic parse-and-
validate over files on disk -- no git repository, no `brd` board, no harness,
no network -- so it sits in the Pure-functions/unit tier alongside
`tests/test_models.py`, not in the Steps tier (`tests/steps/`, temp git repos)
and not in the Adapters tier (`harness/*.py`, a sibling card).

Synthetic bundles are built in `tmp_path` for every error path; the seven shipped
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


def test_unknown_role_raises_naming_the_role(tmp_path):
    make_bundle(tmp_path, "coder")

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("archaeologist", root=tmp_path)

    assert excinfo.value.role == "archaeologist"
    assert "no such role bundle" in excinfo.value.reason
    assert "archaeologist" in str(excinfo.value)


def test_a_file_where_a_bundle_should_be_is_an_unknown_role(tmp_path):
    (tmp_path / "coder").write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "no such role bundle" in excinfo.value.reason


@pytest.mark.parametrize("name", ["..", ".", "../coder", "nested/coder", ""])
def test_a_role_name_that_is_a_path_fragment_is_rejected(tmp_path, name):
    """The name is rejected before it is joined, not incidentally afterwards.

    A fully loadable bundle is planted one level above `root`, so a naive join
    would *succeed* on `"../coder"` rather than fail on a missing file -- the
    assertion on the reason is what keeps this test honest.
    """
    base = tmp_path / "bundles"
    base.mkdir()
    make_bundle(base, "coder")
    make_bundle(tmp_path, "coder", system="Bundle outside the bundles root.\n")

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role(name, root=base)

    assert "not a role name" in excinfo.value.reason
    assert excinfo.value.role == name


def test_an_unreadable_bundle_file_raises_the_loader_error(tmp_path):
    make_bundle(tmp_path)
    (tmp_path / "coder" / "system.md").unlink()
    (tmp_path / "coder" / "system.md").mkdir()

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "unreadable" in excinfo.value.reason
    assert excinfo.value.path.name == "system.md"


def test_list_roles_of_a_directory_that_does_not_exist_is_empty(tmp_path):
    assert loader.list_roles(tmp_path / "nowhere") == []


@pytest.mark.parametrize(
    "omitted, filename",
    [
        ("system", "system.md"),
        ("policy", "policy.toml"),
        ("lock", "VENDORED.lock"),
    ],
)
def test_a_missing_bundle_file_raises_naming_it(tmp_path, omitted, filename):
    make_bundle(tmp_path, **{omitted: None})

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert excinfo.value.path.name == filename
    assert "missing" in excinfo.value.reason


@pytest.mark.parametrize("system", ["", "   \n\t\n"])
def test_an_empty_system_file_raises(tmp_path, system):
    make_bundle(tmp_path, system=system)

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert excinfo.value.path.name == "system.md"
    assert "empty" in excinfo.value.reason


def test_a_non_utf8_system_file_raises_the_loader_error(tmp_path):
    make_bundle(tmp_path)
    (tmp_path / "coder" / "system.md").write_bytes(b"# Coder\n\xff\xfe not utf-8\n")

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "UTF-8" in excinfo.value.reason
    assert excinfo.value.path.name == "system.md"


def test_malformed_policy_toml_raises_with_the_path(tmp_path):
    make_bundle(tmp_path, policy='allowed_tools = ["Read"\nmax_attempts = 1\n')

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "not valid TOML" in excinfo.value.reason
    assert excinfo.value.path.name == "policy.toml"
    assert str(excinfo.value.path) in str(excinfo.value)


@pytest.mark.parametrize(
    "policy",
    [
        # missing max_attempts
        'allowed_tools = ["Read"]\n\n[default_model]\nclaude = "opus"\n',
        # wrong type for max_attempts
        'allowed_tools = ["Read"]\nmax_attempts = "two"\n\n[default_model]\n'
        'claude = "opus"\n',
        # non-positive max_attempts
        'allowed_tools = ["Read"]\nmax_attempts = 0\n\n[default_model]\n'
        'claude = "opus"\n',
        # unknown extra key
        'allowed_tools = ["Read"]\nmax_attempts = 1\nretry_forever = true\n\n'
        '[default_model]\nclaude = "opus"\n',
        # empty allowed-tools list
        "allowed_tools = []\nmax_attempts = 1\n\n[default_model]\n"
        'claude = "opus"\n',
        # empty allowed-tools entry
        'allowed_tools = ["Read", " "]\nmax_attempts = 1\n\n[default_model]\n'
        'claude = "opus"\n',
        # no harness defaults at all
        'allowed_tools = ["Read"]\nmax_attempts = 1\n\n[default_model]\n',
        # unknown-shaped harness key
        'allowed_tools = ["Read"]\nmax_attempts = 1\n\n[default_model]\n'
        '"Claude Code!" = "opus"\n',
        # empty model for a harness
        'allowed_tools = ["Read"]\nmax_attempts = 1\n\n[default_model]\n'
        'claude = ""\n',
    ],
)
def test_policy_schema_violations_raise(tmp_path, policy):
    make_bundle(tmp_path, policy=policy)

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "policy.toml did not validate" in excinfo.value.reason
    assert excinfo.value.path.name == "policy.toml"


def test_malformed_lock_toml_raises_with_the_path(tmp_path):
    make_bundle(tmp_path, lock="[[vendored]\nfile = \n")

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "not valid TOML" in excinfo.value.reason
    assert excinfo.value.path.name == "VENDORED.lock"


@pytest.mark.parametrize(
    "lock",
    [
        # entry missing sha256
        '[[vendored]]\nfile = "tdd.md"\nupstream = "skills/tdd/SKILL.md"\n',
        # entry missing upstream
        '[[vendored]]\nfile = "tdd.md"\n'
        f'sha256 = "{"a" * 64}"\n',
        # wrong type for sha256
        '[[vendored]]\nfile = "tdd.md"\nupstream = "skills/tdd/SKILL.md"\n'
        "sha256 = 5\n",
        # hash that is not a sha256 digest
        '[[vendored]]\nfile = "tdd.md"\nupstream = "skills/tdd/SKILL.md"\n'
        'sha256 = "nope"\n',
        # unknown extra key on the entry
        '[[vendored]]\nfile = "tdd.md"\nupstream = "skills/tdd/SKILL.md"\n'
        f'sha256 = "{"a" * 64}"\nnotes = "hand-edited"\n',
        # unknown top-level key
        'vendored = []\nsource = "somewhere"\n',
    ],
)
def test_lock_schema_violations_raise(tmp_path, lock):
    make_bundle(tmp_path, lock=lock)

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "VENDORED.lock did not validate" in excinfo.value.reason
    assert excinfo.value.path.name == "VENDORED.lock"


def test_a_lock_entry_without_a_file_raises(tmp_path):
    make_bundle(tmp_path, lock=lock_entry("writing-plans.md", "# Writing plans\n"))

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "does not exist" in excinfo.value.reason
    assert excinfo.value.path.name == "writing-plans.md"


def test_a_methodology_file_without_a_lock_entry_raises(tmp_path):
    make_bundle(tmp_path, methodology={"stowaway.md": "# Unlocked text\n"})

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "no VENDORED.lock entry" in excinfo.value.reason
    assert "stowaway.md" in excinfo.value.reason


def test_a_tampered_vendored_file_raises_the_drift_error(tmp_path):
    original = "# TDD\n\nRED, GREEN, REFACTOR.\n"
    make_bundle(
        tmp_path,
        lock=lock_entry("test-driven-development.md", original),
        methodology={"test-driven-development.md": original + "and vibes.\n"},
    )

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "has drifted" in excinfo.value.reason
    assert "test-driven-development.md" in excinfo.value.reason
    assert excinfo.value.path.name == "test-driven-development.md"


def test_duplicate_lock_entries_raise(tmp_path):
    text = "# Writing plans\n\nBite-sized steps.\n"
    make_bundle(
        tmp_path,
        lock=lock_entry("writing-plans.md", text)
        + "\n"
        + lock_entry("writing-plans.md", text, upstream="somewhere/else/SKILL.md"),
        methodology={"writing-plans.md": text},
    )

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "two entries" in excinfo.value.reason
    assert "writing-plans.md" in excinfo.value.reason


@pytest.mark.parametrize("filename", ["../system.md", "nested/tdd.md", ".."])
def test_a_lock_entry_pointing_outside_the_bundle_is_rejected(tmp_path, filename):
    make_bundle(tmp_path, lock=lock_entry(filename, "# anything\n"))

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "not a plain filename" in excinfo.value.reason


def test_a_symlinked_methodology_file_is_rejected(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# Text from outside the bundle\n", encoding="utf-8")
    directory = make_bundle(
        tmp_path, lock=lock_entry("writing-plans.md", outside.read_text())
    )
    (directory / "methodology" / "writing-plans.md").symlink_to(outside)

    with pytest.raises(RoleBundleError) as excinfo:
        loader.load_role("coder", root=tmp_path)

    assert "resolves outside the bundle" in excinfo.value.reason


SHIPPED = [
    "coder",
    "critic",
    "explorer",
    "plan_critic",
    "planner",
    "resolver",
    "reviewer",
    "spec_author",
    "spec_critic",
]

DEFAULT_MODELS = {
    "explorer": "sonnet",
    "spec_author": "opus",
    "planner": "opus",
    "critic": "sonnet",
    "spec_critic": "sonnet",
    "plan_critic": "sonnet",
    "coder": "sonnet",
    "reviewer": "opus",
    "resolver": "opus",
}


def shipped_lock(role: str) -> list[dict[str, str]]:
    """`VENDORED.lock`'s entries for a shipped role, read independently of the
    loader so the drift check does not depend on the code it guards."""
    path = loader.bundles_dir() / role / "VENDORED.lock"
    return tomllib.loads(path.read_text(encoding="utf-8"))["vendored"]


def test_list_roles_returns_exactly_the_nine_shipped_roles():
    assert len(SHIPPED) == 9
    assert loader.list_roles() == SHIPPED


@pytest.mark.parametrize("role", SHIPPED)
def test_every_shipped_bundle_loads(role):
    bundle = loader.load_role(role)

    assert bundle.name == role
    assert bundle.system.strip()
    assert bundle.policy.allowed_tools
    assert bundle.policy.max_attempts > 0


@pytest.mark.parametrize("role", SHIPPED)
def test_every_shipped_bundle_pins_its_claude_model(role):
    assert loader.load_role(role).policy.default_model["claude"] == DEFAULT_MODELS[role]


@pytest.mark.parametrize("role", SHIPPED)
def test_shipped_vendored_files_match_their_recorded_hashes(role):
    directory = loader.bundles_dir() / role

    for entry in shipped_lock(role):
        path = directory / "methodology" / entry["file"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == entry["sha256"], (
            f"{role}/{entry['file']} no longer matches VENDORED.lock; its "
            f"upstream is {entry['upstream']} -- re-sync deliberately"
        )


@pytest.mark.parametrize("role", SHIPPED)
def test_shipped_lock_and_methodology_directory_agree(role):
    directory = loader.bundles_dir() / role

    recorded = sorted(entry["file"] for entry in shipped_lock(role))
    present = sorted(path.name for path in (directory / "methodology").glob("*.md"))

    assert recorded == present


def test_writing_plans_and_tdd_reach_the_roles_that_need_them():
    assert "writing-plans.md" in loader.load_role("planner").methodology
    assert "writing-plans.md" in loader.load_role("spec_author").methodology
    assert "test-driven-development.md" in loader.load_role("coder").methodology
    assert loader.load_role("coder").methodology["test-driven-development.md"].strip()


@pytest.mark.parametrize("role", SHIPPED)
def test_a_shipped_bundle_vendors_only_methodology_its_system_prompt_names(role):
    bundle = loader.load_role(role)

    for filename in bundle.methodology:
        assert filename in bundle.system, (
            f"{role}/system.md never references {filename}, so it should not be "
            "vendored (spec: do not vendor methodology a role does not need)"
        )


def test_the_resolver_policy_allows_editing_and_retries_once_on_opus():
    policy = loader.load_role("resolver").policy

    assert policy.allowed_tools == ["Bash", "Read", "Edit", "Write", "Grep", "Glob"]
    assert policy.max_attempts == 2
    assert policy.default_model == {"claude": "opus"}
    assert policy.required_capabilities == []


def test_the_resolver_vendors_no_methodology():
    directory = loader.bundles_dir() / "resolver"

    assert loader.load_role("resolver").methodology == {}
    assert not (directory / "methodology").exists()
    assert (directory / "VENDORED.lock").read_bytes() == b"vendored = []\n"


@pytest.mark.parametrize(
    "rule",
    [
        "merge is in progress",
        "git diff HEAD...",
        "both stories",
        "git add",
        "git commit --no-edit",
        "git merge --abort",
        "git reset",
        "git checkout",
        "not conflicted",
        "result file",
    ],
)
def test_the_resolver_prompt_states_the_merge_rules(rule):
    # Each phrase carries one rule from the spec's "system.md behaviour" list;
    # losing one lets the agent discard a merge or stop short of committing it.
    assert rule in loader.load_role("resolver").system
