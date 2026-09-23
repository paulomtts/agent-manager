<!-- task-pipeline: validated -->
# Subtask 47bd4ee6 — Role bundle loader and the six bundles

Narrows the milestone design (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`, D6 line 71, §4 lines 129-137, §8 lines 320-334) to one module plus the data it loads. Sibling cards own `harness/` — this card produces only what the Claude adapter will later consume.

## Scope

Deliver `src/agent_manager/roles/loader.py` and six role bundles under `src/agent_manager/roles/bundles/<role>/` for `explorer`, `spec_author`, `planner`, `critic`, `coder`, `reviewer`.

Each bundle directory contains:

- `system.md` — the role's standing instructions, non-empty.
- `policy.toml` — allowed tools, default model per harness, default `max_attempts`, required capabilities.
- `methodology/*.md` — vendored methodology text. At minimum the writing-plans format and the TDD discipline exist as vendored files; a bundle references only the methodology its role actually needs (planner/spec_author need writing-plans, coder needs TDD). Do not vendor an adversarial-review checklist unless a role's `system.md` actually references it.
- `VENDORED.lock` — per vendored methodology file: its upstream path and a content hash of the upstream source.

The loader reads a role name, resolves its bundle directory inside the installed package, parses and validates the three file kinds with Pydantic models (everything here crosses a process boundary — it is parsed off disk), and returns one in-memory bundle object carrying: role name, system prompt text, the policy fields, and the methodology documents keyed by filename with their text already read. No runtime plugin or skill resolution, no network, no filesystem writes, no subprocesses (D6). The loader does not decide routing, does not consult a capability matrix, and does not build prompts or argv — it exposes `required_capabilities` and the per-harness model defaults as data for the engine and adapters to use later.

Out of scope: milestone orchestration (census, levels, parallel stories, integrate), non-Claude harnesses, `harness/base.py`, `harness/launcher.py`, `harness/claude.py`, `build_command`, capability-matrix routing logic, and any CLI surface — the spec describes no CLI for `loader.py`.

## Observable behavior

- Loading a known role returns a validated bundle whose `system` text is the exact bytes of `system.md`, whose policy fields come from `policy.toml`, and whose methodology map contains one entry per `methodology/*.md` file with its exact text. Two loads of the same role produce equal bundles; nothing is mutated on disk.
- Listing available roles returns exactly the six role names, sorted, discovered from the bundles directory rather than hard-coded in a way that can drift from what ships.
- A bundle's declared vendored files and the files actually present under `methodology/` agree: every entry in `VENDORED.lock` names a file that exists, and every `methodology/*.md` file has a lock entry.
- The content hash of each vendored methodology file, recomputed at load/verification time, equals the hash recorded in `VENDORED.lock` for that file. Divergence is an explicit, reported condition — never silently tolerated — so a superpowers update becomes a re-sync decision (§8 lines 330-334).
- Every shipped bundle loads cleanly: the six bundles are themselves part of the contract, not just fixtures.

## Error paths

Every failure is a single, typed loader error carrying the role name, the offending path, and a human-readable reason. Failures are never partial: a bundle either loads whole or raises.

- Unknown role name (no such bundle directory).
- Missing `system.md`, missing `policy.toml`, or missing `VENDORED.lock`.
- Empty or whitespace-only `system.md`.
- Malformed TOML in `policy.toml` (surface the parse error with the path).
- `policy.toml` failing schema validation: missing required key, wrong type, unknown/extra key, non-positive `max_attempts`, empty allowed-tools list, or a model default under an unknown-shaped harness key.
- Malformed or schema-invalid `VENDORED.lock`.
- A `VENDORED.lock` entry pointing at a methodology file that does not exist.
- A `methodology/*.md` file present with no `VENDORED.lock` entry.
- A recorded hash that does not match the vendored file's recomputed hash.
- Path escape: a lock entry or methodology reference that resolves outside the bundle directory is rejected rather than followed.

## Test list

Placement rule: §14 of the design spec (lines 477-492) is this repo's test-placement doc. `roles/loader.py` is deterministic parse-and-validate over files that ship with the package — no git repo, no `brd` board, no harness, no network. That is the **pure-functions unit tier** (closest analog: `workflow/loader.py`, §4 line 123), *not* the Steps tier (temp git repos + temp board) and *not* the Adapters tier (reserved for `harness/*.py` `build_command`, a sibling card's scope). All tests below live in the unit tier, in `tests/roles/test_loader.py`, mirroring `src/agent_manager/roles/` per CLAUDE.md.

1. Loading each of the six roles returns a bundle with non-empty system text — parametrized over the six names. *(unit tier)*
2. Listing roles returns exactly the six expected names. *(unit tier)*
3. A loaded bundle's system text equals the on-disk `system.md` bytes verbatim. *(unit tier)*
4. A loaded bundle exposes the policy fields from `policy.toml`: allowed tools, per-harness default model, `max_attempts`, required capabilities. *(unit tier)*
5. Methodology documents are keyed by filename and carry the file's exact text; a role that needs writing-plans and a role that needs TDD each see theirs. *(unit tier)*
6. **Hash-drift test:** for every shipped bundle, the recomputed hash of each vendored methodology file equals the hash recorded in `VENDORED.lock`. This is the test §8 requires; it is a hash comparison over files already on disk, so it stays in the unit tier. *(unit tier)*
7. Lock/filesystem correspondence for every shipped bundle: no lock entry without a file, no methodology file without a lock entry. *(unit tier)*
8. Unknown role name raises the loader error naming the role. *(unit tier)*
9. Missing `system.md` / missing `policy.toml` / missing `VENDORED.lock` each raise, using a synthetic bundle directory built in `tmp_path`. *(unit tier)*
10. Empty `system.md` and whitespace-only `system.md` each raise. *(unit tier)*
11. Malformed TOML raises with the path in the message. *(unit tier)*
12. Policy schema violations raise — missing key, wrong type, non-positive `max_attempts`, extra key, empty allowed-tools list, a model default under an unknown-shaped harness key — parametrized, against synthetic bundles in `tmp_path`. *(unit tier)*
13. Malformed or schema-invalid `VENDORED.lock` raises — bad syntax and a structurally invalid entry (missing key, wrong type) — parametrized, against synthetic bundles in `tmp_path`. *(unit tier)*
14. A tampered vendored file in a synthetic bundle (text edited so its hash no longer matches the lock) raises the drift error naming the file. *(unit tier)*
15. A lock entry whose path resolves outside the bundle directory is rejected. *(unit tier)*

No Steps-tier, Adapters-tier, Engine-tier or end-to-end tests are added by this card.

## Verification

```bash
uv run pytest
```

No separate typecheck or lint command.

---

# Role Bundle Loader and the Six Bundles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `src/agent_manager/roles/loader.py` plus the six shipped role bundles, so any later caller can turn a role name into a validated, immutable in-memory bundle whose vendored methodology text is provably in sync with its `VENDORED.lock`.

**Architecture:** One module with a single typed error (`RoleBundleError`), four Pydantic models (`Policy`, `VendoredFile`, `VendorLock`, `RoleBundle`) and two public functions (`list_roles`, `load_role`). `load_role` resolves `bundles/<role>/`, reads `system.md` verbatim, parses `policy.toml` and `VENDORED.lock` with `tomllib` then validates them with Pydantic, reads every locked `methodology/*.md`, and verifies both directions of the lock/filesystem correspondence plus every recorded SHA-256 before returning. The loader is built and fully tested against synthetic bundles in `tmp_path` first (Tasks 1-2), then the six real bundles are vendored and asserted through the same public API (Task 3).

**Tech Stack:** Python 3.12+, Pydantic v2, stdlib `tomllib` and `hashlib`, pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-add-the-role-bundle-47bd4ee6-design.md` (reproduced verbatim above); parent design `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (D6 line 71, §4 lines 129-137, §8 lines 320-334, §14 lines 477-492).

## Global Constraints

- Python floor is `requires-python = ">=3.12"` (`pyproject.toml` line 6); `tomllib` is stdlib and needs no dependency addition. Do not add dependencies — `typer>=0.27.2` and `pydantic>=2.9` are the only runtime ones.
- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md). This card's tests all live in `tests/roles/test_loader.py`.
- Pydantic models for anything validated at a process boundary (CLAUDE.md). Everything the loader parses comes off disk, so `policy.toml`, `VENDORED.lock` and the assembled bundle are all Pydantic models, not dataclasses.
- D6 (design line 71): roles are owned by agent-manager and are never resolved from a plugin or skill at runtime. The loader performs no network I/O, no subprocess, and no filesystem writes.
- §8 line 339: "Methodology is never a capability — that is what vendoring is for." `required_capabilities` is carried as data only; no routing or capability-matrix logic here.
- The six role names are exactly `explorer`, `spec_author`, `planner`, `critic`, `coder`, `reviewer` (§8 line 328). `list_roles()` returns them sorted: `["coder", "critic", "explorer", "planner", "reviewer", "spec_author"]`.
- Per-harness default models follow §8 lines 341-344: explorer sonnet, spec_author opus, planner opus, critic sonnet, coder sonnet, reviewer opus.
- Do not create `src/agent_manager/harness/`, `build_command`, or any CLI surface — sibling cards own those.
- Verification is `uv run pytest` only. There is no lint or typecheck command (CLAUDE.md).

## Review Focus

Five failure modes the spec implies but whose numbered test list does not name; each gets a test in the task that owns the code.

1. **Duplicate `file` entries in `VENDORED.lock`** — two entries naming the same methodology file would silently collapse into one map key, and one of the two recorded hashes would never be checked. Expected: a `RoleBundleError` naming the duplicated file. *(Task 2)*
2. **A symlink inside `methodology/` pointing outside the bundle** — the spec's path-escape rule (line 41) is written about lock entries, but a symlinked `methodology/writing-plans.md` escapes the bundle just as effectively while its lock entry looks innocent. Expected: rejected, not followed. *(Task 2)*
3. **Non-UTF-8 bytes in `system.md` or a methodology file** — a bad byte would surface as a raw `UnicodeDecodeError` instead of the single typed loader error the spec's error contract promises (line 30). Expected: `RoleBundleError` naming the path. *(Task 2)*
4. **Non-role directories inside `bundles/`** — a stray `__pycache__/` (Python creates these routinely) would appear in `list_roles()` and break the "exactly the six names" contract in a working tree that has been imported once. Expected: names beginning with `.` or `_` are ignored. *(Task 1)*
5. **A role name that is a path fragment** (`"../coder"`, `".."`, `"a/b"`) — naive joining would escape the bundles directory and load, or half-load, something that is not a bundle. Expected: rejected as an unknown/invalid role rather than resolved. *(Task 2)*

---

## File Structure

- `src/agent_manager/roles/__init__.py` — new, empty package marker (matches `src/agent_manager/steps/__init__.py`).
- `src/agent_manager/roles/loader.py` — new. The entire card's logic: error type, four Pydantic models, `bundles_dir()`, `list_roles()`, `load_role()` and their private readers.
- `src/agent_manager/roles/bundles/<role>/system.md`, `policy.toml`, `VENDORED.lock`, and `methodology/*.md` for the three roles that need vendored text — new data, shipped with the package.
- `tests/roles/test_loader.py` — new. Every test for this card, unit tier (design §14), mirroring `src/agent_manager/roles/`.

No `pyproject.toml` change is required: `[tool.hatch.build.targets.wheel] packages = ["src/agent_manager"]` (lines 24-25) already includes every file under that tree, `.md` and `.toml` alike.

---

### Task 1: The loader module — happy path and role listing

**Files:**
- Create: `src/agent_manager/roles/__init__.py`
- Create: `src/agent_manager/roles/loader.py`
- Test: `tests/roles/test_loader.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. `pydantic.BaseModel`, `ConfigDict`, `Field`, `field_validator`, `ValidationError`; stdlib `hashlib`, `re`, `tomllib`, `pathlib.Path`.
- Produces:
  - `RoleBundleError(RuntimeError)` with `__init__(self, reason: str, *, role: str, path: Path)` and attributes `.reason: str`, `.role: str`, `.path: Path`.
  - `Policy(BaseModel)` with `allowed_tools: list[str]`, `default_model: dict[str, str]`, `max_attempts: int`, `required_capabilities: list[str]`.
  - `VendoredFile(BaseModel)` with `file: str`, `upstream: str`, `sha256: str`.
  - `VendorLock(BaseModel)` with `vendored: list[VendoredFile]`.
  - `RoleBundle(BaseModel)` with `name: str`, `system: str`, `policy: Policy`, `methodology: dict[str, str]`; frozen.
  - `bundles_dir() -> Path`
  - `list_roles(root: Path | None = None) -> list[str]`
  - `load_role(name: str, *, root: Path | None = None) -> RoleBundle`

- [ ] **Step 1: Write the failing happy-path tests**

Create `tests/roles/test_loader.py` with exactly this content (the helpers at the top are used by Tasks 2 and 3 as well):

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/roles/test_loader.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'agent_manager.roles'`.

- [ ] **Step 3: Create the package marker**

Create `src/agent_manager/roles/__init__.py` with exactly this content:

```python
"""Role bundles: the standing instructions, policy and vendored methodology
agent-manager owns for each role (design §8 lines 320-334, decision D6)."""
```

- [ ] **Step 4: Write the loader**

Create `src/agent_manager/roles/loader.py`:

```python
"""Load and validate the role bundles that ship with agent-manager.

Design §8 (lines 320-334) puts a role's standing instructions, its policy and
its vendored methodology under `roles/bundles/<role>/`, and decision D6 (line
71) says roles are *owned* here rather than resolved from a plugin or skill at
dispatch time -- that is what makes one dispatch byte-identical across
harnesses. So this module never touches the network, never spawns a process and
never writes: it reads files that shipped inside the package and validates
them.

Everything parsed here crosses a process boundary (it is text off disk), so the
three file kinds are pydantic models per CLAUDE.md, not dataclasses. A bundle
either loads whole or raises `RoleBundleError`; there is no partially populated
bundle, because a half-loaded role would reach a harness as a silently
truncated system prompt.

Routing is deliberately absent. `required_capabilities` and the per-harness
`default_model` map are carried out as data for the engine and the adapters --
§8 line 339 is explicit that methodology is never a capability.
"""

import hashlib
import re
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

HARNESS_KEY = re.compile(r"^[a-z][a-z0-9_-]*$")
"""Shape of a harness name used as a `[default_model]` key: `claude`, `codex`,
`pi` (§4 line 131). A key outside this shape is a typo or a nested table, and
either would hand the engine a model default nobody can route."""


class RoleBundleError(RuntimeError):
    """Any failure to load one role bundle.

    One error type for all ten failure modes, carrying the role, the offending
    path and a human-readable reason: the caller journals the message, and a
    reason without a path is unactionable when six bundles ship.
    """

    def __init__(self, reason: str, *, role: str, path: Path) -> None:
        self.reason = reason
        self.role = role
        self.path = path
        super().__init__(f"role {role!r}: {reason} ({path})")


class _Model(BaseModel):
    """Frozen and strict about unknown keys.

    `extra="forbid"` is the point of validating a bundle at all: a misspelled
    `max_attempt` that is silently ignored ships a role with the wrong retry
    budget, and nothing downstream would ever notice.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class Policy(_Model):
    """`policy.toml`: what a role may use, and the defaults it dispatches with."""

    allowed_tools: list[str] = Field(min_length=1)
    default_model: dict[str, str] = Field(min_length=1)
    max_attempts: int = Field(gt=0, strict=True)
    required_capabilities: list[str] = Field(default_factory=list)

    @field_validator("allowed_tools", "required_capabilities")
    @classmethod
    def _entries_are_non_empty(cls, value: list[str]) -> list[str]:
        for entry in value:
            if not entry.strip():
                raise ValueError("entries must be non-empty strings")
        return value

    @field_validator("default_model")
    @classmethod
    def _harness_keys_are_well_shaped(cls, value: dict[str, str]) -> dict[str, str]:
        for harness, model in value.items():
            if not HARNESS_KEY.match(harness):
                raise ValueError(f"{harness!r} is not a harness name")
            if not model.strip():
                raise ValueError(f"default model for {harness!r} is empty")
        return value


class VendoredFile(_Model):
    """One `[[vendored]]` entry of `VENDORED.lock` (§8 lines 330-334)."""

    file: str = Field(min_length=1)
    upstream: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class VendorLock(_Model):
    """`VENDORED.lock`: the full manifest of a bundle's vendored methodology.

    An empty list is legitimate -- a role that needs no methodology still ships
    the lock, so "the lock is missing" stays distinguishable from "this role
    vendors nothing".
    """

    vendored: list[VendoredFile] = Field(default_factory=list)


class RoleBundle(_Model):
    """One fully loaded, validated role bundle, ready to be materialised into a
    prompt by whatever adapter later consumes it."""

    name: str = Field(min_length=1)
    system: str = Field(min_length=1)
    policy: Policy
    methodology: dict[str, str] = Field(default_factory=dict)


def bundles_dir() -> Path:
    """The packaged `bundles/` directory, resolved relative to this module."""
    return Path(__file__).parent / "bundles"


def list_roles(root: Path | None = None) -> list[str]:
    """Every role that ships, sorted.

    Discovered from the directory rather than from a constant, so the list can
    never drift from what is actually installed. Names starting with `.` or `_`
    are not roles -- `__pycache__` appears here the moment anything in the tree
    is imported from a checkout.
    """
    base = bundles_dir() if root is None else root
    if not base.is_dir():
        return []
    return sorted(
        entry.name
        for entry in base.iterdir()
        if entry.is_dir() and not entry.name.startswith((".", "_"))
    )


def load_role(name: str, *, root: Path | None = None) -> RoleBundle:
    """Load and validate one role bundle by name.

    `root` overrides the packaged bundles directory; it exists so tests can
    build synthetic bundles, and so an embedder can ship its own set.
    """
    base = bundles_dir() if root is None else root
    directory = base / name
    if not directory.is_dir():
        raise RoleBundleError("no such role bundle", role=name, path=directory)

    system = _read_system(name, directory / "system.md")
    policy = _read_policy(name, directory / "policy.toml")
    methodology = _read_methodology(name, directory)
    return RoleBundle(
        name=name, system=system, policy=policy, methodology=methodology
    )


def _read_text(role: str, path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RoleBundleError("file is missing", role=role, path=path) from exc
    except UnicodeDecodeError as exc:
        raise RoleBundleError(
            "file is not valid UTF-8", role=role, path=path
        ) from exc
    except OSError as exc:
        raise RoleBundleError(
            f"file is unreadable: {exc.strerror}", role=role, path=path
        ) from exc


def _read_system(role: str, path: Path) -> str:
    text = _read_text(role, path)
    if not text.strip():
        raise RoleBundleError("system.md is empty", role=role, path=path)
    return text


def _load_toml(role: str, path: Path) -> dict[str, object]:
    text = _read_text(role, path)
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RoleBundleError(
            f"file is not valid TOML: {exc}", role=role, path=path
        ) from exc


def _read_policy(role: str, path: Path) -> Policy:
    data = _load_toml(role, path)
    try:
        return Policy.model_validate(data)
    except ValidationError as exc:
        raise RoleBundleError(
            f"policy.toml did not validate: {exc}", role=role, path=path
        ) from exc


def _read_methodology(role: str, directory: Path) -> dict[str, str]:
    lock_path = directory / "VENDORED.lock"
    data = _load_toml(role, lock_path)
    try:
        lock = VendorLock.model_validate(data)
    except ValidationError as exc:
        raise RoleBundleError(
            f"VENDORED.lock did not validate: {exc}", role=role, path=lock_path
        ) from exc

    methodology_dir = directory / "methodology"
    documents: dict[str, str] = {}
    for entry in lock.vendored:
        if entry.file in documents:
            raise RoleBundleError(
                f"VENDORED.lock has two entries for {entry.file!r}",
                role=role,
                path=lock_path,
            )
        path = _resolve_methodology(role, methodology_dir, entry.file, lock_path)
        text = _read_text(role, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry.sha256:
            raise RoleBundleError(
                f"vendored {entry.file!r} has drifted from {entry.upstream}: "
                f"VENDORED.lock records {entry.sha256}, the file hashes to "
                f"{digest} -- re-sync deliberately or restore the file",
                role=role,
                path=path,
            )
        documents[entry.file] = text

    _reject_orphan_files(role, methodology_dir, documents)
    return documents


def _resolve_methodology(
    role: str, methodology_dir: Path, filename: str, lock_path: Path
) -> Path:
    """Turn one lock entry's filename into a path inside `methodology/`.

    Two escapes are rejected here rather than followed: a lexical one
    (`../../etc/passwd`) and a symlink whose target leaves the directory. A
    bundle that can reach outside itself is a bundle whose recorded hash proves
    nothing about what a harness will actually be handed.
    """
    if filename in {"", ".", ".."} or filename != Path(filename).name:
        raise RoleBundleError(
            f"lock entry {filename!r} is not a plain filename inside methodology/",
            role=role,
            path=lock_path,
        )

    path = methodology_dir / filename
    if not path.is_file():
        raise RoleBundleError(
            f"VENDORED.lock names {filename!r}, which does not exist",
            role=role,
            path=path,
        )
    if not path.resolve().is_relative_to(methodology_dir.resolve()):
        raise RoleBundleError(
            f"methodology file {filename!r} resolves outside the bundle",
            role=role,
            path=path,
        )
    return path


def _reject_orphan_files(
    role: str, methodology_dir: Path, documents: dict[str, str]
) -> None:
    """Fail on any `methodology/*.md` with no lock entry.

    The correspondence has to hold in both directions: an unlocked file is text
    that reaches a role with nothing recording where it came from, which is
    exactly the silent divergence §8 lines 330-334 exists to prevent.
    """
    if not methodology_dir.is_dir():
        return
    orphans = sorted(
        path.name
        for path in methodology_dir.glob("*.md")
        if path.name not in documents
    )
    if orphans:
        raise RoleBundleError(
            "methodology files with no VENDORED.lock entry: " + ", ".join(orphans),
            role=role,
            path=methodology_dir,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/roles/test_loader.py -v`
Expected: PASS — 9 tests.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS — the pre-existing suite plus the 9 new tests.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/roles/__init__.py src/agent_manager/roles/loader.py tests/roles/test_loader.py
git commit -m "feat(roles): load and validate a role bundle off disk"
```

---

### Task 2: Every error path

**Files:**
- Modify: `src/agent_manager/roles/loader.py` (only if a test below fails against Task 1's implementation)
- Test: `tests/roles/test_loader.py` (append)

**Interfaces:**
- Consumes: `loader.load_role(name, *, root)`, `loader.RoleBundleError` (attributes `.role`, `.path`, `.reason`), and the module-level `make_bundle`, `lock_entry`, `POLICY` helpers from Task 1's test file.
- Produces: no new public API. Task 1's implementation is expected to already satisfy most of these; treat any failure as the RED half of a normal cycle and fix `loader.py`.

- [ ] **Step 1: Write the failing error-path tests**

Append to `tests/roles/test_loader.py`:

```python
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


@pytest.mark.parametrize("name", ["..", "../coder", "nested/coder", ""])
def test_a_role_name_that_is_a_path_fragment_is_rejected(tmp_path, name):
    make_bundle(tmp_path, "coder")
    (tmp_path.parent / "coder").mkdir(exist_ok=True)

    with pytest.raises(RoleBundleError):
        loader.load_role(name, root=tmp_path)


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
```

- [ ] **Step 2: Run the tests and read every failure**

Run: `uv run pytest tests/roles/test_loader.py -v`
Expected: Task 1's loader already implements these paths, so most pass. Any failure is a real gap — fix `src/agent_manager/roles/loader.py` for that case only, then re-run. Do not weaken an assertion to make it pass.

One failure is expected by construction on the `""` case of `test_a_role_name_that_is_a_path_fragment_is_rejected`: `tmp_path / ""` is `tmp_path` itself, which *is* a directory, so `load_role("", root=tmp_path)` would try to load the bundles directory as a bundle. Fix it in `load_role` by validating the name before joining — insert this immediately after the `base = ...` line and before `directory = base / name`:

```python
    if name in {"", ".", ".."} or name != Path(name).name:
        raise RoleBundleError(
            "not a role name", role=name, path=base
        )
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `uv run pytest tests/roles/test_loader.py -v`
Expected: PASS — all error-path tests green alongside Task 1's.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/roles/loader.py tests/roles/test_loader.py
git commit -m "feat(roles): reject every malformed or drifted role bundle"
```

---

### Task 3: The six shipped bundles and their vendored methodology

**Files:**
- Create: `src/agent_manager/roles/bundles/{explorer,spec_author,planner,critic,coder,reviewer}/system.md`
- Create: `src/agent_manager/roles/bundles/{explorer,spec_author,planner,critic,coder,reviewer}/policy.toml`
- Create: `src/agent_manager/roles/bundles/{explorer,spec_author,planner,critic,coder,reviewer}/VENDORED.lock`
- Create: `src/agent_manager/roles/bundles/{planner,spec_author}/methodology/writing-plans.md`, `src/agent_manager/roles/bundles/coder/methodology/test-driven-development.md`
- Test: `tests/roles/test_loader.py` (append)

**Interfaces:**
- Consumes: `loader.load_role(name)` and `loader.list_roles()` with no `root` argument (the packaged default), `loader.bundles_dir()`.
- Produces: the shipped data itself. No new Python API.

- [ ] **Step 1: Write the failing shipped-bundle tests**

Append to `tests/roles/test_loader.py`:

```python
SHIPPED = ["coder", "critic", "explorer", "planner", "reviewer", "spec_author"]

DEFAULT_MODELS = {
    "explorer": "sonnet",
    "spec_author": "opus",
    "planner": "opus",
    "critic": "sonnet",
    "coder": "sonnet",
    "reviewer": "opus",
}


def shipped_lock(role: str) -> list[dict[str, str]]:
    """`VENDORED.lock`'s entries for a shipped role, read independently of the
    loader so the drift check does not depend on the code it guards."""
    path = loader.bundles_dir() / role / "VENDORED.lock"
    return tomllib.loads(path.read_text(encoding="utf-8"))["vendored"]


def test_list_roles_returns_exactly_the_six_shipped_roles():
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/roles/test_loader.py -v`
Expected: FAIL — `test_list_roles_returns_exactly_the_six_shipped_roles` asserts `[] == [...]`, and every parametrized shipped-bundle test errors because `bundles/` does not exist yet.

- [ ] **Step 3: Write the six `system.md` files**

Run this from the worktree root to create the directories:

```bash
mkdir -p src/agent_manager/roles/bundles/{explorer,spec_author,planner,critic,coder,reviewer}
```

Create `src/agent_manager/roles/bundles/explorer/system.md`:

```markdown
# Explorer

You investigate a codebase and report what is actually there. You do not change
it.

- Read the card, then read the code the card names. Follow imports and callers
  until you can name every file the work will touch.
- Report file paths with line numbers. A claim without a path is a guess.
- Name the existing conventions the work must follow: the test tier it belongs
  in, the module that already does something similar, the error type in use.
- Say plainly when something the card assumes does not exist.
- Never edit, create or delete a file, and never run a command that writes.
```

Create `src/agent_manager/roles/bundles/spec_author/system.md`:

```markdown
# Spec author

You turn one card and its exploration findings into a design spec that fixes
observable behavior.

- Write what the software must do, not how to code it: observable behavior,
  error paths, and the list of tests that will prove it.
- Every test in your list names the tier it belongs in and why.
- Stay inside the card. Name what is out of scope explicitly, including the work
  that belongs to sibling cards.
- Follow the plan format in `methodology/writing-plans.md` for anything your
  spec hands to a planner.
- Cite the parent design spec by section and line for every constraint you
  inherit.
```

Create `src/agent_manager/roles/bundles/planner/system.md`:

```markdown
# Planner

You turn a spec into a TDD implementation plan another engineer can execute with
no context of their own.

- Follow `methodology/writing-plans.md` exactly: bite-sized steps, real code in
  every step, RED before GREEN, a commit at the end of each task.
- Read every file a step will touch before you write that step. Exact paths, no
  placeholders.
- Never write "TBD", "handle errors appropriately", or "similar to the task
  above" -- repeat the code instead.
- Prepend the spec to the plan, then check the plan back against it.
```

Create `src/agent_manager/roles/bundles/critic/system.md`:

```markdown
# Critic

You adversarially review a spec or a plan before anyone writes code.

- Hunt for the requirement with no test, the placeholder wearing a confident
  sentence, the type named in one task and renamed in another.
- Check that every test lands in the tier its spec entry named.
- Report findings as a list, each with a file path, what is wrong, and what
  would make it right. No praise, no summary of what is fine.
- Judge only what is in front of you. Do not redesign the work.
```

Create `src/agent_manager/roles/bundles/coder/system.md`:

```markdown
# Coder

You execute an implementation plan, one task at a time, under strict TDD.

- Follow `methodology/test-driven-development.md`: write the failing test, watch
  it fail for the right reason, write the minimum code that passes, watch it
  pass, commit.
- Never weaken an assertion to make a test pass, and never delete a failing test
  you did not write.
- Implement the task in front of you and nothing else. Work the plan's steps
  fully rather than skipping ahead.
- Run the project's verification command before claiming a task is done, and
  report its real output.
```

Create `src/agent_manager/roles/bundles/reviewer/system.md`:

```markdown
# Reviewer

You review finished work against the plan and the spec it came from.

- Read the diff in full. Check each plan task actually landed, with its tests.
- Look for behavior the spec required that nothing exercises, and for tests that
  assert the implementation rather than the requirement.
- Verify the claims: run the verification command yourself and quote the output.
- Return findings ordered by severity, each with a file path and a concrete fix.
  If the work is sound, say so and stop.
```

- [ ] **Step 4: Write the six `policy.toml` files**

Create `src/agent_manager/roles/bundles/explorer/policy.toml`:

```toml
allowed_tools = ["Read", "Grep", "Glob", "Bash"]
max_attempts = 1
required_capabilities = []

[default_model]
claude = "sonnet"
```

Create `src/agent_manager/roles/bundles/spec_author/policy.toml`:

```toml
allowed_tools = ["Read", "Grep", "Glob", "Write"]
max_attempts = 2
required_capabilities = []

[default_model]
claude = "opus"
```

Create `src/agent_manager/roles/bundles/planner/policy.toml`:

```toml
allowed_tools = ["Read", "Grep", "Glob", "Write"]
max_attempts = 2
required_capabilities = []

[default_model]
claude = "opus"
```

Create `src/agent_manager/roles/bundles/critic/policy.toml`:

```toml
allowed_tools = ["Read", "Grep", "Glob"]
max_attempts = 1
required_capabilities = []

[default_model]
claude = "sonnet"
```

Create `src/agent_manager/roles/bundles/coder/policy.toml`:

```toml
allowed_tools = ["Read", "Grep", "Glob", "Edit", "Write", "Bash"]
max_attempts = 3
required_capabilities = []

[default_model]
claude = "sonnet"
```

Create `src/agent_manager/roles/bundles/reviewer/policy.toml`:

```toml
allowed_tools = ["Read", "Grep", "Glob", "Bash"]
max_attempts = 1
required_capabilities = []

[default_model]
claude = "opus"
```

- [ ] **Step 5: Vendor the methodology text and generate every `VENDORED.lock`**

Run this from the worktree root. It copies the two upstream superpowers skills into the bundles that reference them and writes all six lock files, hashing what it copied:

```bash
python - <<'PY'
import hashlib
import shutil
from pathlib import Path

VERSION = "6.4.1"
UPSTREAM_ROOT = (
    Path.home() / ".claude/plugins/cache/claude-plugins-official/superpowers" / VERSION
)
BUNDLES = Path("src/agent_manager/roles/bundles")
VENDORED = {
    "explorer": [],
    "spec_author": [("writing-plans.md", "skills/writing-plans/SKILL.md")],
    "planner": [("writing-plans.md", "skills/writing-plans/SKILL.md")],
    "critic": [],
    "coder": [
        ("test-driven-development.md", "skills/test-driven-development/SKILL.md")
    ],
    "reviewer": [],
}

for role, files in VENDORED.items():
    directory = BUNDLES / role
    tables = []
    for filename, relative in files:
        source = UPSTREAM_ROOT / relative
        target = directory / "methodology" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        tables.append(
            "[[vendored]]\n"
            f'file = "{filename}"\n'
            f'upstream = "superpowers/{VERSION}/{relative}"\n'
            f'sha256 = "{digest}"\n'
        )
    text = "\n".join(tables) if tables else "vendored = []\n"
    (directory / "VENDORED.lock").write_text(text, encoding="utf-8")
    print(f"{role}: {len(files)} vendored file(s)")
PY
```

Expected output:

```
explorer: 0 vendored file(s)
spec_author: 1 vendored file(s)
planner: 1 vendored file(s)
critic: 0 vendored file(s)
coder: 1 vendored file(s)
reviewer: 0 vendored file(s)
```

If the script raises `FileNotFoundError` on `UPSTREAM_ROOT`, the installed superpowers version differs from `6.4.1`: run `ls ~/.claude/plugins/cache/claude-plugins-official/superpowers/`, set `VERSION` to the directory you find, and re-run. The version must stay in the `upstream` string — that string is what a future re-sync reads.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/roles/test_loader.py -v`
Expected: PASS — every shipped-bundle test green.

If `test_a_shipped_bundle_vendors_only_methodology_its_system_prompt_names` fails, a `system.md` does not mention the filename its bundle vendors. Fix the `system.md` (Step 3's planner, spec_author and coder prompts each name theirs); do not delete the assertion.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 8: Confirm the drift test actually bites**

Run:

```bash
printf '\nHand-edited line.\n' >> src/agent_manager/roles/bundles/coder/methodology/test-driven-development.md
uv run pytest tests/roles/test_loader.py -k "coder and (loads or hashes)" -v
```

Expected: FAIL — both `test_shipped_vendored_files_match_their_recorded_hashes[coder]` and `test_every_shipped_bundle_loads[coder]` fail, the latter with "has drifted". Then restore the file and confirm green:

```bash
git checkout -- src/agent_manager/roles/bundles/coder/methodology/test-driven-development.md 2>/dev/null || true
python - <<'PY'
from pathlib import Path

path = Path(
    "src/agent_manager/roles/bundles/coder/methodology/test-driven-development.md"
)
text = path.read_text(encoding="utf-8")
path.write_text(text.replace("\nHand-edited line.\n", ""), encoding="utf-8")
PY
uv run pytest tests/roles/test_loader.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/roles/bundles tests/roles/test_loader.py
git commit -m "feat(roles): ship the six role bundles with vendored methodology"
```

---

## Done when

- `uv run pytest` passes from the worktree root.
- `src/agent_manager/roles/loader.py` exists with `RoleBundleError`, `Policy`, `VendoredFile`, `VendorLock`, `RoleBundle`, `bundles_dir`, `list_roles`, `load_role`.
- Six bundles ship, `loader.list_roles()` returns exactly `["coder", "critic", "explorer", "planner", "reviewer", "spec_author"]`, and every one of them loads.
- Nothing under `src/agent_manager/harness/` was created, and no CLI command was added.
