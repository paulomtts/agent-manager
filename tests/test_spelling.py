"""The legacy cancel spelling `cancelled` as a string value in
`src/agent_manager`: allowed only as `models.LEGACY_CANCELED` and as the cancel
comment's dedup key in `comments.py`. Docstrings and comments are not scanned.
"""

import ast
from collections import Counter
from pathlib import Path

import pytest

import agent_manager

PACKAGE = Path(agent_manager.__file__).parent
LEGACY = "cancelled"
MODELS_SITE = "models.py: LEGACY_CANCELED = 'cancelled'"
COMMENTS_SITE = "comments.py: key(..., 'cancelled')"


def _is_legacy(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == LEGACY


def _allowed_sites(rel: str, tree: ast.Module) -> dict[int, str]:
    """`id` of each allowed `Constant` in one module, mapped to its site label."""
    sites: dict[int, str] = {}
    for node in ast.walk(tree):
        if rel == "models.py" and isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if (
                len(targets) == 1
                and isinstance(targets[0], ast.Name)
                and targets[0].id == "LEGACY_CANCELED"
                and node.value is not None
                and _is_legacy(node.value)
            ):
                sites[id(node.value)] = MODELS_SITE
        if (
            rel == "comments.py"
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "key"
        ):
            for arg in node.args:
                if _is_legacy(arg):
                    sites[id(arg)] = COMMENTS_SITE
    return sites


def _scan(sources: dict[str, str]) -> tuple[list[str], Counter[str]]:
    """Offending `cancelled` string constants, and how often each allowed site was found.

    `sources` maps a path relative to the package to that module's source.
    """
    offending: list[str] = []
    found: Counter[str] = Counter()
    for rel, source in sources.items():
        tree = ast.parse(source)
        docstrings = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }
        allowed = _allowed_sites(rel, tree)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and LEGACY in node.value.lower()
            ):
                continue
            if id(node) in docstrings:
                continue
            if id(node) in allowed:
                found[allowed[id(node)]] += 1
            else:
                offending.append(f"{rel}:{node.lineno}: {node.value!r}")
    return sorted(offending), found


def test_legacy_cancel_spelling_only_at_allowed_sites():
    """Every `cancelled` string constant under `src/agent_manager` is one of the
    two allowed sites, and each allowed site is found exactly once."""
    sources = {
        path.relative_to(PACKAGE).as_posix(): path.read_text(encoding="utf-8")
        for path in PACKAGE.rglob("*.py")
    }

    offending, found = _scan(sources)

    assert offending == []
    assert found == {MODELS_SITE: 1, COMMENTS_SITE: 1}


_ALLOWED_SOURCES = {
    "models.py": 'LEGACY_CANCELED = "cancelled"\n',
    "comments.py": 'comment_key = key(run_id, card_id, "cancelled")\n',
}


@pytest.mark.parametrize(
    ("rel", "extra", "expected"),
    [
        ("runs.py", 'status = "cancelled"\n', ["runs.py:1: 'cancelled'"]),
        ("runs.py", 'msg = f"run cancelled {x}"\n', ["runs.py:1: 'run cancelled '"]),
        ("runs.py", 'label = "Cancelled"\n', ["runs.py:1: 'Cancelled'"]),
        ("cli.py", 'BAD = "CANCELLED"\n', ["cli.py:1: 'CANCELLED'"]),
        ("models.py", 'OTHER = "cancelled"\n', ["models.py:2: 'cancelled'"]),
        ("comments.py", 'status = "cancelled"\n', ["comments.py:2: 'cancelled'"]),
        ("runs.py", 'X = 1\n"""Read as cancelled."""\n', []),
        ("runs.py", '"""Module: cancelled runs."""\n', []),
        (
            "runs.py",
            "try:\n    pass\nexcept asyncio.CancelledError:  # cancelled\n    pass\nf.cancelled()\n",
            [],
        ),
    ],
    ids=[
        "plain-literal",
        "f-string-part",
        "mixed-case",
        "upper-case",
        "second-literal-in-models",
        "second-literal-in-comments",
        "attribute-docstring",
        "module-docstring",
        "comment-and-identifier",
    ],
)
def test_scan_reports_only_non_docstring_literals_outside_allowed_sites(rel, extra, expected):
    """Literals, f-string parts and any case of `cancelled` are reported with path
    and line, also in the files that hold an allowed site; docstrings, comments
    and identifiers are not."""
    sources = dict(_ALLOWED_SOURCES)
    sources[rel] = sources.get(rel, "") + extra

    offending, found = _scan(sources)

    assert offending == expected
    assert found == {MODELS_SITE: 1, COMMENTS_SITE: 1}


@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        ({"models.py": 'LEGACY = "cancelled"\n'}, {}),
        ({"comments.py": "k = key(run_id, card_id, LEGACY)\n"}, {}),
        (
            {
                "comments.py": 'a = key(r, c, "cancelled")\nb = key(r, d, "cancelled")\n',
            },
            {COMMENTS_SITE: 2},
        ),
    ],
    ids=["models-target-renamed", "dedup-key-not-a-literal", "dedup-key-twice"],
)
def test_scan_counts_each_allowed_site(sources, expected):
    """A renamed, removed or duplicated allowed site shows in the site counts,
    which the guard requires to be exactly one each."""
    _, found = _scan(sources)

    assert found == expected
