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
