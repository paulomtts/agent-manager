"""Consistency of the audit suite under `.claude/`: checklist frontmatter, check
ids, citation form, the workflow's phases, the unit map and the `.gitignore`
exceptions. Pure file reading; unit tier."""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / ".claude" / "skills" / "audit"
DIMENSIONS = SKILL_DIR / "dimensions"
SKILL = SKILL_DIR / "SKILL.md"
WORKFLOW = REPO / ".claude" / "workflows" / "audit.js"

AXIS_PREFIX = {
    "placement-boundaries": "PB",
    "economy": "EC",
    "honesty": "HO",
    "single-source-of-truth": "SS",
    "test-quality": "TQ",
}
CHECK_KEYS = {"id", "name", "was", "detection", "scopes", "applies-to", "guard"}
REQUIRED_CHECK_KEYS = {"id", "name", "detection", "scopes", "applies-to"}
EXCLUSIONS = ("!docs/superpowers/**", "!uv.lock", "!.claude/worktrees/**")
CITATION_PREFIX = "docs/standards/architecture.md "
FOREIGN_PATTERNS = (
    r"app/modules",
    r"pjx",
    r"htmx",
    r"ruff",
    r"tenant",
    r"\borg\b",
    r"\bori\b",
)
PROVENANCE_HEADING = "## Provenance and adaptations"


class _Flow:
    """Parser for the YAML flow subset the checklists use: `{k: v}`, `[a, b]`,
    single-quoted and bare scalars. Raises ValueError on anything else."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def parse(self) -> object:
        value = self._value()
        self._skip()
        if self.pos != len(self.text):
            raise ValueError(f"trailing text: {self.text[self.pos:]!r}")
        return value

    def _skip(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] == " ":
            self.pos += 1

    def _peek(self) -> str:
        if self.pos >= len(self.text):
            raise ValueError(f"unexpected end: {self.text!r}")
        return self.text[self.pos]

    def _value(self) -> object:
        self._skip()
        char = self._peek()
        if char == "{":
            return self._mapping()
        if char == "[":
            return self._sequence()
        if char == "'":
            return self._quoted()
        return self._bare(",]}")

    def _quoted(self) -> str:
        self.pos += 1
        out = []
        while True:
            char = self._peek()
            self.pos += 1
            if char == "'":
                if self.pos < len(self.text) and self.text[self.pos] == "'":
                    out.append("'")
                    self.pos += 1
                    continue
                return "".join(out)
            out.append(char)

    def _bare(self, stops: str) -> str:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] not in stops:
            self.pos += 1
        scalar = self.text[start : self.pos].strip()
        if not scalar:
            raise ValueError(f"empty scalar at {start} in {self.text!r}")
        return scalar

    def _mapping(self) -> dict[str, object]:
        self.pos += 1
        result: dict[str, object] = {}
        while True:
            self._skip()
            if self._peek() == "}":
                self.pos += 1
                return result
            key = self._bare(":")
            self.pos += 1
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = self._value()
            self._skip()
            if self._peek() == ",":
                self.pos += 1

    def _sequence(self) -> list[object]:
        self.pos += 1
        result: list[object] = []
        while True:
            self._skip()
            if self._peek() == "]":
                self.pos += 1
                return result
            result.append(self._value())
            self._skip()
            if self._peek() == ",":
                self.pos += 1


def parse_frontmatter(text: str) -> dict[str, object]:
    """The frontmatter between the leading `---` lines. Top-level `key: value`
    lines, and `checks:` followed by `  - {...}` flow mappings."""
    lines = text.splitlines()
    if lines[0] != "---":
        raise ValueError("no frontmatter")
    end = lines.index("---", 1)
    data: dict[str, object] = {}
    for line in lines[1:end]:
        if line.startswith("  - "):
            data.setdefault("checks", [])
            data["checks"].append(_Flow(line[4:]).parse())  # type: ignore[union-attr]
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        if key in data:
            raise ValueError(f"duplicate key {key!r}")
        if key == "checks":
            data[key] = []
        elif raw.startswith("["):
            data[key] = _Flow(raw).parse()
        else:
            data[key] = raw
    return data


def _checklists() -> dict[str, dict[str, object]]:
    return {
        path.parent.name: parse_frontmatter(path.read_text(encoding="utf-8"))
        for path in sorted(DIMENSIONS.glob("*/CHECKLIST.md"))
    }


def _all_checks() -> list[tuple[str, dict[str, object]]]:
    return [(axis, check) for axis, data in _checklists().items() for check in data["checks"]]


def _check_ids() -> list[str]:
    return [f"{axis}:{check['id']}" for axis, check in _all_checks()]


def _suite_texts() -> dict[Path, str]:
    paths = [SKILL, WORKFLOW, *sorted(DIMENSIONS.rglob("*.md"))]
    return {path: path.read_text(encoding="utf-8") for path in paths}


def _without_provenance(skill_text: str) -> str:
    start = skill_text.index(PROVENANCE_HEADING)
    end = skill_text.index("\n## ", start + len(PROVENANCE_HEADING))
    return skill_text[:start] + skill_text[end:]


def test_the_suite_ships_exactly_the_pilot_axes():
    assert set(_checklists()) == set(AXIS_PREFIX)


@pytest.mark.parametrize("axis", sorted(AXIS_PREFIX))
def test_checklist_frontmatter_declares_its_axis_and_checks(axis):
    data = _checklists()[axis]
    assert data["name"] == axis
    assert data["description"] and data["principle"]
    assert isinstance(data["standards"], list) and data["standards"]
    assert data["checks"]


@pytest.mark.parametrize("ident", _check_ids())
def test_check_entry_keys_follow_the_schema(ident):
    axis, check_id = ident.split(":")
    check = next(c for a, c in _all_checks() if a == axis and c["id"] == check_id)
    assert REQUIRED_CHECK_KEYS <= set(check) <= CHECK_KEYS


def test_check_ids_are_unique_across_axes():
    ids = [check["id"] for _, check in _all_checks()]
    assert len(ids) == len(set(ids))


def test_check_ids_carry_their_axis_prefix():
    for axis, check in _all_checks():
        assert re.fullmatch(rf"{AXIS_PREFIX[axis]}[0-9]+", check["id"]), check["id"]


def test_detection_and_scopes_use_the_closed_vocabulary():
    for _, check in _all_checks():
        assert check["detection"] in {"mechanical", "judgment"}, check["id"]
        assert check["scopes"] and set(check["scopes"]) <= {"diff", "unit"}, check["id"]


def test_mechanical_checks_name_a_guard_that_is_pending_or_exists():
    for _, check in _all_checks():
        if check["detection"] == "mechanical":
            guard = check["guard"]
            assert guard == "pending" or (REPO / guard).is_file(), check["id"]
        else:
            assert "guard" not in check, check["id"]


def test_applies_to_excludes_history_lockfile_and_worktrees():
    for _, check in _all_checks():
        globs = [g.strip() for g in check["applies-to"].split(",")]
        for exclusion in EXCLUSIONS:
            assert exclusion in globs, (check["id"], exclusion)


def test_every_check_has_a_body_section_and_no_section_is_orphaned():
    for axis, data in _checklists().items():
        body = (DIMENSIONS / axis / "CHECKLIST.md").read_text(encoding="utf-8")
        headings = set(re.findall(r"^### ([A-Z]+[0-9]+) (\S+)$", body, re.MULTILINE))
        declared = {(check["id"], check["name"]) for check in data["checks"]}
        assert headings == declared, axis


def test_section_citations_name_the_architecture_standard():
    for path, text in _suite_texts().items():
        if path.suffix == ".md":
            text = re.sub(r"^```.*?^```$", "", text, flags=re.MULTILINE | re.DOTALL)
        for match in re.finditer(r"§", text):
            if not re.match(r"§[0-9]", text[match.start() :]):
                continue
            assert text[: match.start()].endswith(CITATION_PREFIX), (
                path.name,
                text[max(0, match.start() - 40) : match.start() + 6],
            )
            assert re.match(r"§[0-9]+(\.[0-9]+)*\b", text[match.start() :]), path.name


def test_suite_files_carry_no_strings_from_the_source_repo():
    for path, text in _suite_texts().items():
        if path == SKILL:
            text = _without_provenance(text)
        for pattern in FOREIGN_PATTERNS:
            hit = re.search(pattern, text, re.IGNORECASE)
            assert hit is None, (path.name, pattern, hit and hit.group(0))


def test_workflow_declares_no_fix_phase():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.findall(r"title: '(\w+)'", text) == ["Scope", "Select", "Audit", "Verify", "Bundle"]
    assert "phase('Fix')" not in text
    for forbidden in ("gh pr create", "git push", "isolation:", "fixModel", "baseBranch"):
        assert forbidden not in text, forbidden


def test_workflow_refuses_to_run_without_report_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "if (params.reportOnly !== true) {\n  throw new Error(" in text
    assert "if (params.fix !== undefined && params.fix !== false) {\n  throw new Error(" in text


def test_skill_always_passes_report_only():
    text = _without_provenance(SKILL.read_text(encoding="utf-8"))
    assert "reportOnly: true" in text
    assert "fix:" not in text


def _unit_map() -> dict[str, list[str]]:
    text = SKILL.read_text(encoding="utf-8")
    section = text[text.index("### Unit map") : text.index("## Step 2")]
    units: dict[str, list[str]] = {}
    for line in section.splitlines():
        row = re.fullmatch(r"\| ([a-z]+) \| (.+) \|", line)
        if row:
            units[row.group(1)] = re.findall(r"`([^`]+)`", row.group(2))
    return units


def _covered_by(path: str, pathspec: str) -> bool:
    return path == pathspec or path.startswith(pathspec.rstrip("/") + "/")


def test_unit_map_paths_exist():
    for unit, pathspecs in _unit_map().items():
        for pathspec in pathspecs:
            if pathspec.startswith(("src/", "tests/")) or pathspec in {"README.md", "CLAUDE.md"}:
                assert (REPO / pathspec).exists(), (unit, pathspec)


def test_seam_unit_holds_the_named_modules():
    assert {
        "src/agent_manager/cli.py",
        "src/agent_manager/orchestrate.py",
        "src/agent_manager/runs.py",
        "src/agent_manager/bases.py",
        "src/agent_manager/integration.py",
        "tests/test_cli.py",
        "tests/test_orchestrate.py",
        "tests/test_bases.py",
        "tests/test_integration.py",
    } <= set(_unit_map()["seam"])


def test_every_python_file_belongs_to_exactly_one_unit():
    units = _unit_map()
    files = [
        path.relative_to(REPO).as_posix()
        for root in (REPO / "src" / "agent_manager", REPO / "tests")
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    for path in files:
        owners = [u for u, specs in units.items() if any(_covered_by(path, s) for s in specs)]
        assert len(owners) == 1, (path, owners)


@dataclass(frozen=True)
class _Rule:
    negate: bool
    parts: tuple[str, ...]
    dir_only: bool
    anchored: bool

    def matches(self, parts: list[str], is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        if not self.anchored:
            return fnmatchcase(parts[-1], self.parts[0])
        return len(parts) == len(self.parts) and all(
            fnmatchcase(part, pattern) for part, pattern in zip(parts, self.parts)
        )


def _gitignore_rules() -> list[_Rule]:
    rules = []
    for raw in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line.lstrip("!")
        dir_only = pattern.endswith("/")
        pattern = pattern.strip("/")
        rules.append(_Rule(negate, tuple(pattern.split("/")), dir_only, "/" in pattern))
    return rules


def _ignored(path: str) -> bool:
    """Git's verdict for a file path under the repo's root `.gitignore`: last
    matching rule wins, and nothing under an ignored directory is re-included."""
    rules = _gitignore_rules()
    parts = path.split("/")
    for depth in range(1, len(parts) + 1):
        prefix = parts[:depth]
        is_dir = depth < len(parts)
        verdict = False
        for rule in rules:
            if rule.matches(prefix, is_dir):
                verdict = not rule.negate
        if verdict:
            return True
    return False


@pytest.mark.parametrize(
    "path",
    [
        ".claude/skills/audit/SKILL.md",
        ".claude/skills/audit/dimensions/honesty/CHECKLIST.md",
        ".claude/workflows/audit.js",
    ],
)
def test_gitignore_tracks_the_audit_skill_and_workflow(path):
    assert not _ignored(path)


@pytest.mark.parametrize(
    "path",
    [
        ".claude/worktrees/feat-x/src/agent_manager/cli.py",
        ".claude/settings.local.json",
        ".claude/settings.json",
        ".claude/skills/other/SKILL.md",
        ".claude/workflows/other.js",
        ".claude/cache/entry",
        "docs/superpowers/specs/x-design.md",
    ],
)
def test_gitignore_keeps_everything_else_under_claude_ignored(path):
    assert _ignored(path)


def test_gitignore_re_includes_only_the_audit_paths():
    negated = {"/".join(rule.parts) for rule in _gitignore_rules() if rule.negate}
    assert negated == {
        ".claude/skills",
        ".claude/skills/audit",
        ".claude/workflows",
        ".claude/workflows/audit.js",
    }


def test_every_suite_file_on_disk_is_tracked():
    for path in [WORKFLOW, *SKILL_DIR.rglob("*")]:
        if path.is_file():
            assert not _ignored(path.relative_to(REPO).as_posix()), path
