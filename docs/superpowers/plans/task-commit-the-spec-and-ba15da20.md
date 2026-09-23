<!-- task-pipeline: validated -->
# Commit the spec and plan with the Plan-Hash trailer (card ba15da20)

## 1. Why this exists

Today nothing in the engine commits the spec and the plan. On the real branch the fake `claude` harness does it, which violates real-harness spec R4 (the fake must know no more than the brief and must not do work the engine owes) and leaves the production wiring untested. This subtask moves that commit into a deterministic step the engine runs, and makes that commit carry the `Plan-Hash` trailer that design §9's resume contract is built on: a commit with the current hash is resumed from, an untagged commit is debris.

Sibling `f26b377d` owns everything downstream of this step — the `plan_hash` phase *input* in `prompt.py`, adding it to `implement`'s `inputs:` in `task.yaml`, the coder role text, and teaching the fake harness to read the hash out of its brief. None of that belongs here. This card produces the hash, the commit, and the context entry the sibling will read.

## 2. Scope

One new module `src/agent_manager/steps/docs_commit.py`, its registration, one new deterministic phase in `workflow/builtin/task.yaml`, and tests. Nothing else in `src/` changes except `workflow/registry.py` and the document.

Out of scope, restated so it is not drifted into: milestone orchestration, parallel stories, `integrate`, non-Claude harnesses, ancestor rollup, counting reviews with git, and any change to `steps/reducers.py:plan_hash_gate` / `is_plan_hash` (the step must stay faithful to that definition, not redefine it).

## 3. The step

Public surface, mirroring the injection convention of `steps/worktree.py` (`GitRunner` parameter defaulting to a real `run_git`, so tests can observe argv while behaviour tests still use a real repo):

- a pure helper that takes the plan file's bytes (or path) and returns the first 8 lowercase hex characters of their SHA-256 — the same shape `reducers.is_plan_hash` accepts, and the value `review` will independently recompute.
- the step entry point, whose parameter names are what the engine binds by name from `subtask_context` plus `_document_paths`: `card_details` (the full `models.Card`, for `title`), `spec_path`, `plan_path` (repo-relative strings, already expanded from the phases' `writes:` templates by `prompt.expand_writes` and placed in the context by `engine._document_paths`), `worktree` (absolute path the git calls run in), and `git_runner` with a default. No `args:` in the document are needed — every value is already in the binding table under exactly these names. The step never touches the board and never reads `brd`.

Returns a plain dict `{"plan_hash": "<8 hex>"}` — a `Mapping`, as `engine._run_deterministic` requires, stored in the context under the phase name, so later phases read `context["docs_commit"]["plan_hash"]`. Plain dict, not Pydantic, per `CLAUDE.md` (internal-only state).

### Observable behaviour

1. Resolve `spec_path` and `plan_path` against `worktree`. Both must exist as files by the time this runs — the `spec` and `plan` agent phases wrote them, and `validate_plan` (and, once it lands, `mark_validated`) has already appended the validated marker.
2. Compute the plan hash from the plan file's bytes **as they are on disk at this moment**, i.e. with the validated marker already present. That ordering is the whole reason the phase sits after `mark_validated`: a hash taken before the marker would never match what `review` recomputes.
3. Stage exactly the two declared paths: one `git add --` invocation naming `spec_path` and `plan_path` and nothing else. `git add -A`, `git add .` and pathspecs with wildcards are forbidden; an unrelated stray or untracked file in the worktree must survive uncommitted.
4. If anything is staged for those two paths, commit with subject `docs: add spec and plan for <card title>`, a blank line, and a final trailer line `Plan-Hash: <hash>`. `<card title>` is `card_details.title` verbatim. The trailer is the last line of the message.
5. Return `{"plan_hash": hash}`.

### Idempotence (design §9 resume)

If step 3 produced nothing to commit for those two paths **and** the branch already carries a commit whose message contains this exact `Plan-Hash: <hash>` line, the step commits nothing and returns the same hash. That is the resumed-run path: re-running the phase after a kill is a no-op, and the commit count does not grow.

If there is nothing to commit but no commit on the branch carries this hash, that is not a silent success — the documents are tracked and unchanged but untagged. The step commits nothing it cannot commit; it raises (see error paths) rather than returning a hash that no commit on the branch corroborates, because `review`'s untagged-commit branch would otherwise be reading a lie.

### Error paths

- `spec_path` or `plan_path` missing, blank, not a string, or absent on disk → `ValueError` naming the offending path, raised before any git call.
- `worktree` not an absolute path, or `card_details` absent / carrying an empty title → `ValueError` before any git call (same pre-flight style as `worktree._required_absolute`).
- Either path escaping the worktree after resolution → `ValueError`; the expander already refuses `..` and absolutes, and the step does not trust that twice over.
- Any git invocation exiting non-zero → the `GitError` from the runner propagates unchanged. `engine._run_deterministic` catches everything and records the phase failed, so no swallowing here.
- Nothing to commit and no matching trailer on the branch → a raised error whose message names the hash and both paths.

## 4. Wiring

`src/agent_manager/workflow/registry.py`: add the step's dotted name to `BUILTIN_FUNCTION_NAMES` (sorted tuple) and `registry.register(...)` it in `default_registry()` with the **real imported callable**, in the "steps that already ship on this branch" block — not a `_placeholder`.

`src/agent_manager/workflow/builtin/task.yaml`: a new `kind: deterministic` phase with `run:` pointing at the registered name, placed after `mark_validated` and before `implement`. If `mark_validated` is not present on the branch this lands on, place it after `validate_plan` and before `implement`; the invariant is "after the marker is written, before the coder runs".

Branch state (verified): `mark_validated` (`plan_check.mark_validated`, registered) is already in `task.yaml` and `registry.py`, and `tests/e2e/` exists with `fake_claude.py` and `test_production_wiring.py`. The new phase goes directly after `mark_validated` and before `implement`; no fallback placement is needed.

`tests/e2e/fake_claude.py`: its `spec`/`plan` branches already only write documents. The only commit it makes is in the `implement` branch (`git add -A` then `git commit` with a `Plan-Hash` trailer computed via `plan_hash_of`), which is the coder's implementation commit, not the docs commit. That implement-side behaviour, and teaching the fake to read the hash from its brief, belongs to sibling `f26b377d`; leave it alone. What this card must guarantee is that the docs commit is made by the engine step before `implement` runs, so the fake's `add -A` no longer sweeps spec and plan into its commit. Adjust only what breaks: existing e2e assertions (e.g. `test_production_wiring.py` commit counts / `review["commit_count"] == len(revisions)` and tagged counts) must be updated to include the new docs commit.

## 5. Tests

Tier per design §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`, Testing). Tiers used: *unit* (pure functions), *steps* (real temporary git repo under `tmp_path`, no network, faking only what a real process cannot produce), *workflow/document*, *wiring* (default-suite production-wiring test with a fake `claude` on `PATH`).

In `tests/steps/test_docs_commit.py`:

1. The hash helper returns exactly 8 lowercase hex characters for known bytes, matches `hashlib.sha256(...).hexdigest()[:8]`, and satisfies `reducers.is_plan_hash`. — *unit* (pure function, same file is fine).
2. Changing one byte of the plan changes the hash; the marker being present is part of the hashed bytes. — *unit*.
3. Happy path in a real `tmp_path` repo: writes spec and plan, runs the step, exactly one new commit exists, its subject is `docs: add spec and plan for <title>`, its last line is `Plan-Hash: <returned hash>`, and the returned hash equals the helper's hash of the plan file. — *steps*.
4. Only the two declared paths are committed: an unrelated untracked file and an unrelated modified tracked file in the worktree are still uncommitted afterwards, and `git show --name-only` lists exactly the spec and plan. — *steps*.
5. Idempotent resume: calling the step twice in a row yields the same hash, and the commit count is unchanged by the second call. — *steps*.
6. Nothing to commit and no trailer on the branch (documents committed by an earlier commit with no `Plan-Hash`) raises, naming the hash. — *steps*.
7. Error pre-flight: missing plan file, missing spec file, relative `worktree`, missing/blank card title each raise `ValueError` and run no git command (assert against an injected runner that records argv and would fail the test if called). — *steps*.
8. No forbidden git verb: with a recording runner, assert no invocation contains `add -A`/`add .`, `reset`, `clean`, `checkout -f`, or `push`. — *steps*.

In `tests/workflow/`:

9. `tests/workflow/test_registry.py` (~line 84): the new name appears in the expected-name list and `resolve(name)` **is** the imported callable, not a placeholder. — *workflow/document*.
10. `tests/workflow/test_builtin_task.py`: update `EXPECTED_PHASES` and the phase-count test; add a document test asserting the new phase's index is after `mark_validated` (after `validate_plan` if the former is absent) and before `implement`, is `kind: deterministic`, and its `run:` is the registered name. — *workflow/document*.

In `tests/e2e/test_production_wiring.py` (default suite, fake `claude` on `PATH`):

11. After a full run, the docs commit exists with subject `docs: add spec and plan for <title>` and a `Plan-Hash:` trailer, and it precedes the fake harness's implement commit in `git log` (the fake no longer commits documents). — *wiring*.
12. `git status --porcelain` in the worktree is clean after the run. — *wiring*.

The whole default suite (`uv run pytest`) must stay green; no new test is opt-in or slow, and the single slow real-harness end-to-end test stays excluded as it is.

---

# Docs Commit with the Plan-Hash Trailer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic `docs_commit` phase that hashes the validated plan, commits exactly the spec and the plan with a `Plan-Hash` trailer, and is a no-op on resume.

**Architecture:** One new module `src/agent_manager/steps/docs_commit.py` holding a pure `plan_hash(content: bytes) -> str` helper and a `commit_documents(...)` step that takes an injected `GitRunner` exactly the way `steps/worktree.py` does. The step is bound by parameter name out of the engine's existing binding table (`card_details`, `spec_path`, `plan_path`, `worktree`), so the new YAML phase needs no `args:`. It is registered in `workflow/registry.py` and placed in `workflow/builtin/task.yaml` between `mark_validated` and `implement`.

**Tech Stack:** Python 3, `hashlib`, `subprocess` (via the existing `steps.worktree.run_git`), pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-commit-the-spec-and-ba15da20-design.md` (reproduced verbatim above).

## Global Constraints

- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (`CLAUDE.md`).
- Plain dicts/dataclasses for internal-only state; Pydantic only at process boundaries (`CLAUDE.md`). The step returns a plain `dict`.
- Verification is exactly `uv run pytest`. There is no separate lint or typecheck command.
- A Plan-Hash is **the first 8 lowercase hex characters of the sha256 of the plan file's bytes** — the definition `steps/reducers.py:is_plan_hash` enforces (`re.fullmatch(r"[0-9a-f]{8}")`). Do not redefine it, do not change `reducers.py`.
- `git add -A`, `git add .`, `reset`, `clean`, `checkout -f` and `push` are forbidden verbs inside the step.
- The commit subject is exactly `docs: add spec and plan for <card title>`; the trailer is exactly `Plan-Hash: <hash>` and is the last line of the message.
- The step never reads the board and never runs `brd`.
- Registered function name: `docs_commit.commit_documents`. Phase name in the document: `docs_commit`.
- Out of scope (sibling `f26b377d`): the `plan_hash` phase *input* in `prompt.py`, adding it to `implement`'s `inputs:`, the coder role text, and teaching the fake harness to read the hash from its brief.

## Review Focus

- **A resumed run whose plan file changed between runs** — the second call stages a real diff and must make a *second* commit with the *new* hash rather than silently returning the old one. Pinned in Task 4.
- **An unrelated file already staged in the index when the step runs** — it must not be swept into the docs commit. Pinned in Task 3 (the commit carries an explicit pathspec, so a partial commit is what git performs).
- **A `card_details` of `None`** (the engine sets that key to `None` whenever `cli` did not fetch the card) — must be a pre-flight `ValueError`, not an `AttributeError` mid-git. Pinned in Task 5.
- **A `spec_path`/`plan_path` containing `..`** — even though `prompt.expand_writes` already refuses it, the step must refuse a path that resolves outside the worktree rather than staging a file in the parent repo. Pinned in Task 5.
- **A worktree whose real path differs from the given path by a symlinked parent** (macOS `/tmp` → `/private/tmp`, and `tmp_path` itself) — the containment check must compare `os.path.realpath` on both sides or every steps-tier test fails spuriously. Pinned in Task 5.

---

### Task 1: The pure plan-hash helper

**Files:**
- Create: `src/agent_manager/steps/docs_commit.py`
- Test: `tests/steps/test_docs_commit.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `docs_commit.plan_hash(content: bytes) -> str` — the first 8 lowercase hex characters of `sha256(content)`. Used by Task 3's step and by Task 3–5 tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/steps/test_docs_commit.py` with exactly this content:

```python
"""Behaviour of the docs-commit step (design §4 `steps/`, spec card ba15da20).

Placement follows design §14: `plan_hash` is a pure function and is unit-tested
here directly, while `commit_documents` is a Steps component and is exercised
against a real temporary git repository under `tmp_path` -- no network, and git
is faked only where a test must observe argv that a real run would also produce.
"""

import hashlib

from agent_manager.steps import docs_commit, reducers


def test_plan_hash_is_the_first_eight_hex_of_the_sha256() -> None:
    content = b"# plan\n\n<!-- task-pipeline: validated -->\n"
    assert docs_commit.plan_hash(content) == hashlib.sha256(content).hexdigest()[:8]


def test_plan_hash_has_the_shape_the_reducer_accepts() -> None:
    digest = docs_commit.plan_hash(b"anything at all")
    assert len(digest) == 8
    assert digest == digest.lower()
    assert reducers.is_plan_hash(digest)


def test_plan_hash_changes_when_one_byte_of_the_plan_changes() -> None:
    """The validated marker is part of the hashed bytes, which is why the phase
    has to run AFTER `mark_validated` -- a hash taken before the marker would
    never match what `review` recomputes."""
    before = b"# plan\n\nbody\n"
    after = b"# plan\n\nbody\n<!-- task-pipeline: validated -->\n"
    assert docs_commit.plan_hash(before) != docs_commit.plan_hash(after)
    assert docs_commit.plan_hash(b"a") != docs_commit.plan_hash(b"b")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_docs_commit.py -v`
Expected: FAIL — `ImportError: cannot import name 'docs_commit' from 'agent_manager.steps'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/steps/docs_commit.py`:

```python
"""Commit the spec and the plan, tagged with the plan's Plan-Hash trailer.

A deterministic step (design §4 `steps/`, §6): git and the filesystem only --
no model call, no board access, no `brd`. It runs after `mark_validated` and
before `implement`, because the hash it stamps is the hash of the plan file
*with* the validated marker already on disk, which is exactly the hash `review`
recomputes independently (design §9).

Every invocation is an argument list handed to `subprocess` through the
injected `GitRunner` (the seam `steps/worktree.py` established): there is no
shell string and nothing to quote.
"""

import hashlib

_HASH_LENGTH = 8
"""How many characters of the digest a Plan-Hash is.

`reducers.is_plan_hash` accepts exactly 8 lowercase hex characters and nothing
else; this constant is that contract's other half and must never drift from it.
"""


def plan_hash(content: bytes) -> str:
    """The first 8 lowercase hex characters of `content`'s SHA-256.

    Pure, and the single definition of the hash in this module: the step reads
    the plan's bytes off disk and hands them here, so a test can pin the value
    without a repository.

    `hexdigest()` is lowercase by construction; the slice is taken from it
    rather than from a re-cased string so there is no place for a stray
    uppercase character to enter and fail `reducers.is_plan_hash`.
    """
    return hashlib.sha256(content).hexdigest()[:_HASH_LENGTH]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_docs_commit.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/docs_commit.py tests/steps/test_docs_commit.py
git commit -m "feat(steps): add the pure plan_hash helper"
```

---

### Task 2: The step commits exactly the two documents with the trailer

**Files:**
- Modify: `src/agent_manager/steps/docs_commit.py`
- Test: `tests/steps/test_docs_commit.py`

**Interfaces:**
- Consumes: `docs_commit.plan_hash(content: bytes) -> str` (Task 1); `agent_manager.steps.worktree.GitRunner`, `worktree.run_git`, `worktree.GitError`.
- Produces: `docs_commit.commit_documents(card_details, spec_path, plan_path, worktree, git_runner=run_git) -> dict[str, object]` returning `{"plan_hash": "<8 hex>"}`; module constants `docs_commit.SUBJECT_TEMPLATE` (`"docs: add spec and plan for {title}"`) and `docs_commit.TRAILER_PREFIX` (`"Plan-Hash: "`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_docs_commit.py` (the imports at the top of the file grow to the full set this task needs — replace the existing import block with the one below, then append the rest):

```python
import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_manager.steps import docs_commit, reducers

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the docs-commit step's steps-tier tests",
)

SPEC_RELATIVE = "docs/superpowers/specs/task-commit-the-spec-and-ba15da20.md"
PLAN_RELATIVE = "docs/superpowers/plans/task-commit-the-spec-and-ba15da20.md"
TITLE = "Commit the spec and plan with the Plan-Hash trailer"


@dataclass
class FakeCard:
    """The one field the step reads off `models.Card`.

    A stand-in rather than the real model on purpose: the step must bind to any
    object carrying a `title`, and a steps-tier test should not depend on the
    board's schema.
    """

    title: str


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in `cwd` for test setup, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo on `main` with one commit, isolated in tmp_path."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "base")
    return root


def _write_documents(root: Path) -> None:
    """The spec and the plan exactly where the `writes:` templates put them."""
    for relative, body in (
        (SPEC_RELATIVE, "# spec\n\nthe design.\n"),
        (PLAN_RELATIVE, "# plan\n\nthe steps.\n<!-- task-pipeline: validated -->\n"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _run(root: Path, **overrides):
    """Call the step against `root` with the standard arguments."""
    arguments = {
        "card_details": FakeCard(title=TITLE),
        "spec_path": SPEC_RELATIVE,
        "plan_path": PLAN_RELATIVE,
        "worktree": str(root),
    }
    arguments.update(overrides)
    return docs_commit.commit_documents(**arguments)


def _commit_count(root: Path) -> int:
    return len(_git(root, "rev-list", "HEAD").split())


def _message(root: Path, revision: str = "HEAD") -> str:
    return _git(root, "show", "-s", "--format=%B", revision).rstrip("\n")


def _recorder(calls: list[list[str]], inner=None):
    """A git runner that records every argv, optionally delegating to `inner`."""

    def runner(argv: list[str]) -> str:
        calls.append(list(argv))
        return "" if inner is None else inner(argv)

    return runner


@requires_git
def test_the_step_makes_one_commit_whose_subject_and_trailer_are_exact(repo: Path) -> None:
    _write_documents(repo)
    before = _commit_count(repo)

    result = _run(repo)

    assert _commit_count(repo) == before + 1
    message = _message(repo)
    assert message.splitlines()[0] == f"docs: add spec and plan for {TITLE}"
    assert message.splitlines()[-1] == f"Plan-Hash: {result['plan_hash']}"


@requires_git
def test_the_returned_hash_is_the_hash_of_the_plan_file_on_disk(repo: Path) -> None:
    _write_documents(repo)

    result = _run(repo)

    expected = hashlib.sha256((repo / PLAN_RELATIVE).read_bytes()).hexdigest()[:8]
    assert result == {"plan_hash": expected}
    assert reducers.is_plan_hash(result["plan_hash"])


@requires_git
def test_only_the_two_declared_documents_are_committed(repo: Path) -> None:
    """A stray untracked file and an unrelated modified tracked file both
    survive uncommitted: the step names its two pathspecs and never sweeps."""
    _write_documents(repo)
    (repo / "stray.txt").write_text("not mine\n", encoding="utf-8")
    (repo / "README.md").write_text("edited by somebody else\n", encoding="utf-8")

    _run(repo)

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == sorted([SPEC_RELATIVE, PLAN_RELATIVE])
    porcelain = _git(repo, "status", "--porcelain")
    assert "stray.txt" in porcelain
    assert "README.md" in porcelain


@requires_git
def test_an_unrelated_already_staged_file_is_not_swept_into_the_docs_commit(
    repo: Path,
) -> None:
    """Review focus: the index may already hold somebody else's change when the
    step runs. `git commit -- <paths>` is a partial commit, so it cannot."""
    _write_documents(repo)
    (repo / "README.md").write_text("staged by somebody else\n", encoding="utf-8")
    _git(repo, "add", "README.md")

    _run(repo)

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == sorted([SPEC_RELATIVE, PLAN_RELATIVE])
    assert "README.md" in _git(repo, "diff", "--cached", "--name-only")


@requires_git
def test_the_step_runs_no_forbidden_git_verb(repo: Path) -> None:
    """Design §9: this step may add and commit. Sweeping (`add -A`, `add .`) or
    destroying (`reset`, `clean`, `checkout -f`) or publishing (`push`) is how a
    resumed run loses a human's work."""
    _write_documents(repo)
    calls: list[list[str]] = []

    # `docs_commit.run_git` is the same real runner the default argument uses,
    # so the step still drives a real repository while every argv is observed.
    _run(repo, git_runner=_recorder(calls, docs_commit.run_git))

    assert calls  # non-vacuity: a step that ran no git at all would pass emptily
    for argv in calls:
        assert "-A" not in argv, argv
        assert "--all" not in argv, argv
        for verb in ("reset", "clean", "push", "rm", "restore"):
            assert verb not in argv, argv
        if "add" in argv:
            assert "." not in argv, argv
            assert "*" not in " ".join(argv), argv
        if "checkout" in argv:
            assert "-f" not in argv, argv
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_docs_commit.py -v`
Expected: FAIL — `AttributeError: module 'agent_manager.steps.docs_commit' has no attribute 'commit_documents'` on the five new tests; the three Task 1 tests still pass.

- [ ] **Step 3: Write the minimal implementation**

Append to `src/agent_manager/steps/docs_commit.py` (and extend its import block to the one shown first):

```python
import hashlib
from pathlib import Path

from agent_manager.steps.worktree import GitRunner, run_git
```

```python
SUBJECT_TEMPLATE = "docs: add spec and plan for {title}"
"""The docs commit's subject line. One template, so the step that writes it and
any future reader that greps for it cannot disagree about the wording."""

TRAILER_PREFIX = "Plan-Hash: "
"""The trailer `review` and `reducers.review_gate` look for, verbatim.

The trailing space is part of it: `Plan-Hash:a1b2c3d4` is not a git trailer and
would not be counted by anything downstream.
"""


def _document_paths(worktree_path: str, spec_path: str, plan_path: str) -> tuple[Path, Path]:
    """The two documents as absolute paths under the worktree."""
    root = Path(worktree_path)
    return root / spec_path, root / plan_path


def commit_documents(
    card_details: object,
    spec_path: str,
    plan_path: str,
    worktree: str | Path,
    git_runner: GitRunner = run_git,
) -> dict[str, object]:
    """Commit the spec and the plan, tagged with the plan's Plan-Hash.

    Parameter names are the engine's binding table's names, not invented ones:
    `card_details`, `worktree` come from `engine.subtask_context` and
    `spec_path` / `plan_path` from `engine._document_paths`, so the document's
    phase needs no `args:` at all.

    The return value is the deterministic phase's result (design §6) -- a plain
    dict, since it crosses no process boundary and so needs no Pydantic model
    (`CLAUDE.md`). It lands in the context under the phase name, so a later
    phase reads `context["docs_commit"]["plan_hash"]`.
    """
    worktree_path = str(worktree)
    title = getattr(card_details, "title", None)
    spec_file, plan_file = _document_paths(worktree_path, spec_path, plan_path)

    digest = plan_hash(plan_file.read_bytes())

    # `--` and then exactly two literal pathspecs. Never `-A`, never `.`: an
    # unrelated file a human left in the worktree is theirs, not this commit's.
    git_runner(["-C", worktree_path, "add", "--", spec_path, plan_path])
    # The pathspec on `commit` too, so an unrelated change already sitting in
    # the index is a partial commit's leftover rather than part of this one.
    git_runner(
        [
            "-C",
            worktree_path,
            "commit",
            "-m",
            SUBJECT_TEMPLATE.format(title=title),
            "-m",
            f"{TRAILER_PREFIX}{digest}",
            "--",
            spec_path,
            plan_path,
        ]
    )
    return {"plan_hash": digest}
```

Two `-m` arguments are how git produces "subject, blank line, trailer": git joins them with a blank line, so the trailer is the last line of the message with no manual `\n\n` to get wrong.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_docs_commit.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/docs_commit.py tests/steps/test_docs_commit.py
git commit -m "feat(steps): commit the spec and plan with a Plan-Hash trailer"
```

---

### Task 3: Idempotent on resume, loud when nothing corroborates the hash

**Files:**
- Modify: `src/agent_manager/steps/docs_commit.py`
- Test: `tests/steps/test_docs_commit.py`

**Interfaces:**
- Consumes: `docs_commit.commit_documents(...)` and `docs_commit.TRAILER_PREFIX` (Task 2).
- Produces: `docs_commit.UntaggedDocumentsError(RuntimeError)` with attributes `plan_hash: str`, `spec_path: str`, `plan_path: str`; raised when there is nothing to commit and no commit reachable from `HEAD` carries the trailer.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_docs_commit.py`:

```python
@requires_git
def test_a_second_call_commits_nothing_and_returns_the_same_hash(repo: Path) -> None:
    """Design §9 resume: re-running the phase after a kill is a no-op, and the
    commit count does not grow."""
    _write_documents(repo)
    first = _run(repo)
    after_first = _commit_count(repo)

    second = _run(repo)

    assert second == first
    assert _commit_count(repo) == after_first


@requires_git
def test_a_plan_edited_between_runs_gets_its_own_commit_with_the_new_hash(
    repo: Path,
) -> None:
    """Review focus: the resume path is "nothing staged", not "ran before". A
    plan whose bytes changed has a different hash and must be committed again,
    or every trailer on the branch would be stale."""
    _write_documents(repo)
    first = _run(repo)
    (repo / PLAN_RELATIVE).write_text("# plan\n\nrewritten.\n", encoding="utf-8")
    after_first = _commit_count(repo)

    second = _run(repo)

    assert second["plan_hash"] != first["plan_hash"]
    assert _commit_count(repo) == after_first + 1
    assert _message(repo).splitlines()[-1] == f"Plan-Hash: {second['plan_hash']}"


@requires_git
def test_documents_committed_without_a_trailer_raise_instead_of_lying(
    repo: Path,
) -> None:
    """Nothing to commit AND no commit carrying this hash is not a silent
    success: `review`'s untagged-commit branch would be reading a lie."""
    _write_documents(repo)
    _git(repo, "add", "--", SPEC_RELATIVE, PLAN_RELATIVE)
    _git(repo, "commit", "-m", "docs: committed by a human, untagged")
    before = _commit_count(repo)

    with pytest.raises(docs_commit.UntaggedDocumentsError) as caught:
        _run(repo)

    expected = hashlib.sha256((repo / PLAN_RELATIVE).read_bytes()).hexdigest()[:8]
    assert caught.value.plan_hash == expected
    message = str(caught.value)
    assert expected in message
    assert SPEC_RELATIVE in message
    assert PLAN_RELATIVE in message
    assert _commit_count(repo) == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_docs_commit.py -v`
Expected: FAIL — `test_a_second_call_commits_nothing_and_returns_the_same_hash` and `test_documents_committed_without_a_trailer_raise_instead_of_lying` both fail, the first with `GitError` ("nothing to commit"), the second with `AttributeError: module 'agent_manager.steps.docs_commit' has no attribute 'UntaggedDocumentsError'`. `test_a_plan_edited_between_runs_gets_its_own_commit_with_the_new_hash` already passes.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/docs_commit.py`, add the error class next to the constants:

```python
class UntaggedDocumentsError(RuntimeError):
    """The documents are committed, but no commit on the branch carries this hash.

    Raised rather than returning the hash: `review` counts commits whose message
    holds a `Plan-Hash` trailer, so answering with a hash nothing corroborates
    would make a resumed run read a branch as tagged when it is debris
    (design §9).
    """

    def __init__(self, *, plan_hash: str, spec_path: str, plan_path: str) -> None:
        self.plan_hash = plan_hash
        self.spec_path = spec_path
        self.plan_path = plan_path
        super().__init__(
            f"{spec_path} and {plan_path} have nothing to commit, but no commit on "
            f"this branch carries the trailer {TRAILER_PREFIX}{plan_hash}. The "
            "documents are tracked and untagged, so a resumed run would read them "
            "as debris. Commit them with the trailer, or remove them, by hand."
        )
```

Then add the two helpers and rewrite the tail of `commit_documents` — replace the `git_runner(["-C", worktree_path, "add", ...])`-to-`return` block with:

```python
    git_runner(["-C", worktree_path, "add", "--", spec_path, plan_path])

    staged = git_runner(
        [
            "-C",
            worktree_path,
            "diff",
            "--cached",
            "--name-only",
            "--",
            spec_path,
            plan_path,
        ]
    )
    if staged.strip() == "":
        # The resume path (design §9). "Ran before" is not the test -- "these
        # two paths hold no change" is, so a plan edited between runs still
        # earns its own commit with its own hash, two lines above.
        if _branch_carries(git_runner, worktree_path, digest):
            return {"plan_hash": digest}
        raise UntaggedDocumentsError(
            plan_hash=digest, spec_path=spec_path, plan_path=plan_path
        )

    git_runner(
        [
            "-C",
            worktree_path,
            "commit",
            "-m",
            SUBJECT_TEMPLATE.format(title=title),
            "-m",
            f"{TRAILER_PREFIX}{digest}",
            "--",
            spec_path,
            plan_path,
        ]
    )
    return {"plan_hash": digest}
```

and define `_branch_carries` above `commit_documents`:

```python
def _branch_carries(git_runner: GitRunner, worktree_path: str, digest: str) -> bool:
    """Whether any commit reachable from HEAD carries exactly this trailer.

    Whole-line equality on the stripped line, never substring containment: a
    commit whose body merely *discusses* `Plan-Hash: a1b2c3d4` must not be
    mistaken for a tagged one, and a longer hash ending in these eight
    characters must not match either.

    A `GitError` here (an empty repository has no `HEAD` to log) is answered
    `False` rather than swallowed into a success: "I could not find a commit
    carrying this hash" is precisely what an unreadable log means.
    """
    try:
        log = git_runner(["-C", worktree_path, "log", "--format=%B"])
    except GitError:
        return False
    wanted = f"{TRAILER_PREFIX}{digest}"
    return any(line.strip() == wanted for line in log.splitlines())
```

and extend the module's import of `worktree` to bring in `GitError`:

```python
from agent_manager.steps.worktree import GitError, GitRunner, run_git
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_docs_commit.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/docs_commit.py tests/steps/test_docs_commit.py
git commit -m "feat(steps): make the docs commit idempotent on resume"
```

---

### Task 4: Pre-flight validation before any git call

**Files:**
- Modify: `src/agent_manager/steps/docs_commit.py`
- Test: `tests/steps/test_docs_commit.py`

**Interfaces:**
- Consumes: `docs_commit.commit_documents(...)` (Tasks 2–3).
- Produces: no new public names. `commit_documents` now raises `ValueError` for a blank/non-string document path, a missing document, a non-absolute or non-existent `worktree`, a `card_details` without a non-blank `title`, and a document path that resolves outside the worktree — all before the first `git_runner` call.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_docs_commit.py`:

```python
def _exploding_runner(argv: list[str]) -> str:
    raise AssertionError(f"pre-flight must run no git command, but ran: {argv!r}")


@requires_git
@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"plan_path": "docs/superpowers/plans/absent.md"}, "absent.md"),
        ({"spec_path": "docs/superpowers/specs/absent.md"}, "absent.md"),
        ({"spec_path": ""}, "spec_path"),
        ({"plan_path": "   "}, "plan_path"),
        ({"plan_path": None}, "plan_path"),
        ({"worktree": "relative/worktree"}, "worktree"),
        ({"card_details": None}, "card_details"),
        ({"card_details": FakeCard(title="  ")}, "title"),
        ({"spec_path": "../escape.md"}, "escape.md"),
    ],
)
def test_a_bad_argument_raises_value_error_before_any_git_runs(
    repo: Path, overrides: dict, needle: str
) -> None:
    _write_documents(repo)

    with pytest.raises(ValueError) as caught:
        _run(repo, git_runner=_exploding_runner, **overrides)

    assert needle in str(caught.value)


@requires_git
def test_a_worktree_reached_through_a_symlink_still_works(
    repo: Path, tmp_path: Path
) -> None:
    """Review focus: `tmp_path` (and `/tmp` on macOS) can sit behind a symlink,
    so containment has to be decided on `realpath`, not on the literal string."""
    _write_documents(repo)
    link = tmp_path / "link-to-repo"
    link.symlink_to(repo, target_is_directory=True)

    result = _run(repo, worktree=str(link))

    assert reducers.is_plan_hash(result["plan_hash"])
    assert _commit_count(repo) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_docs_commit.py -v`
Expected: FAIL — the parametrised cases fail with `FileNotFoundError`, `TypeError`, `AssertionError` from `_exploding_runner`, or a `GitError`, but not `ValueError`. The symlink test may already pass.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/docs_commit.py`, add these three pre-flight helpers above `commit_documents`:

```python
def _required_relative_path(value: object, field: str) -> str:
    """A non-blank relative document path, or `ValueError` before any git call."""
    if not isinstance(value, (str, Path)):
        raise ValueError(
            f"docs_commit.commit_documents needs a path string for {field}, "
            f"got {value!r}"
        )
    text = str(value).strip()
    if text == "":
        raise ValueError(
            f"docs_commit.commit_documents needs a non-empty {field}, got {value!r}"
        )
    if Path(text).is_absolute():
        raise ValueError(
            f"docs_commit.commit_documents needs a worktree-relative {field}, but "
            f"{text!r} is absolute; `git add --` would stage a file outside the run"
        )
    return text


def _required_worktree(value: object) -> str:
    """An existing absolute worktree directory, or `ValueError` up front.

    The same pre-flight shape `worktree.ensure` and `verify.run_suite` use: a
    bad path must fail loudly here, not as a confusing `git -C` failure.
    """
    if not isinstance(value, (str, Path)):
        raise ValueError(
            f"docs_commit.commit_documents needs an absolute worktree path, "
            f"got {value!r}"
        )
    text = str(value).strip()
    if text == "" or not Path(text).is_absolute() or not Path(text).is_dir():
        raise ValueError(
            f"docs_commit.commit_documents needs an existing absolute worktree "
            f"directory, got {value!r}"
        )
    return text


def _required_title(card_details: object) -> str:
    """The card's non-blank title, or `ValueError` before any git call.

    `engine.subtask_context` binds `card_details` to `None` whenever the caller
    supplied no card, so `None` is a real runtime shape and must not surface as
    an `AttributeError` halfway through a commit message.
    """
    title = getattr(card_details, "title", None)
    if not isinstance(title, str) or title.strip() == "":
        raise ValueError(
            "docs_commit.commit_documents needs card_details carrying a non-blank "
            f"title for the commit subject, got {card_details!r}"
        )
    return title


def _inside(root: str, candidate: Path, field: str) -> Path:
    """`candidate`, proven to live under `root`, or `ValueError`.

    `realpath` on both sides: a tmp directory (and `/tmp` on macOS) can sit
    behind a symlink, so a literal string comparison would reject a perfectly
    good worktree. `prompt.expand_writes` already refuses `..`; this does not
    trust that twice over, because a `git add` outside the worktree would stage
    a file in the parent repository.
    """
    real_root = Path(os.path.realpath(root))
    real_candidate = Path(os.path.realpath(candidate))
    if real_root != real_candidate and real_root not in real_candidate.parents:
        raise ValueError(
            f"docs_commit.commit_documents refuses {field} {str(candidate)!r}: it "
            f"resolves to {str(real_candidate)!r}, which is outside the worktree "
            f"{root!r}"
        )
    return real_candidate
```

Add `import os` to the module's imports, and replace the first four lines of `commit_documents`' body (from `worktree_path = str(worktree)` through `digest = plan_hash(plan_file.read_bytes())`) with:

```python
    worktree_path = _required_worktree(worktree)
    spec_path = _required_relative_path(spec_path, "spec_path")
    plan_path = _required_relative_path(plan_path, "plan_path")
    title = _required_title(card_details)

    spec_file, plan_file = _document_paths(worktree_path, spec_path, plan_path)
    spec_file = _inside(worktree_path, spec_file, "spec_path")
    plan_file = _inside(worktree_path, plan_file, "plan_path")
    for field, path in (("spec_path", spec_file), ("plan_path", plan_file)):
        if not path.is_file():
            raise ValueError(
                f"docs_commit.commit_documents cannot find the {field} document at "
                f"{str(path)!r}; the `{field.removesuffix('_path')}` phase was "
                "supposed to write it"
            )

    digest = plan_hash(plan_file.read_bytes())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_docs_commit.py -v`
Expected: PASS (21 passed).

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS — nothing outside `tests/steps/test_docs_commit.py` touches the new module yet.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/steps/docs_commit.py tests/steps/test_docs_commit.py
git commit -m "feat(steps): pre-flight the docs commit before any git call"
```

---

### Task 5: Register the step and put the phase in the document

**Files:**
- Modify: `src/agent_manager/workflow/registry.py:19` (import), `:210-223` (`BUILTIN_FUNCTION_NAMES`), `:254-261` (`default_registry`)
- Modify: `src/agent_manager/workflow/builtin/task.yaml:57-61` and the `plan_check` phase's `skip_to` (line 27)
- Test: `tests/workflow/test_registry.py:84-97`, `:119-152`
- Test: `tests/workflow/test_builtin_task.py:43-57`, `:91-96` (skip_to assertion), `:67-69`, `:99-119`, `:352-372`

**Interfaces:**
- Consumes: `docs_commit.commit_documents` (Tasks 2–4).
- Produces: the registered name `"docs_commit.commit_documents"` in `BUILTIN_FUNCTION_NAMES` and `default_registry()`, and the `docs_commit` phase in `builtin/task.yaml` between `mark_validated` and `implement`.

- [ ] **Step 1: Write the failing tests**

In `tests/workflow/test_registry.py`, add `docs_commit` to the steps import on line 9:

```python
from agent_manager.steps import docs_commit, plan_check, reducers, rollup, verify, worktree
```

add the new name to `TASK_YAML_NAMES` (line 84, keeping it sorted — `d` sits between `critic_blockers_gate` and `exploration_output_gate`):

```python
TASK_YAML_NAMES = (
    "critic_blockers_gate",
    "docs_commit.commit_documents",
    "exploration_output_gate",
    "plan_check.find_validated_plan",
    "plan_check.has_validated_plan",
    "plan_check.mark_validated",
    "plan_hash_gate",
    "review_gate",
    "rollup.set_status",
    "verification_gate",
    "verification_passed_gate",
    "verify.run_suite",
    "worktree.ensure",
)
```

add one line to `test_default_registry_resolves_implemented_steps_to_the_real_callables` (after the `rollup.set_status` assertion on line 127):

```python
    assert registry.resolve("docs_commit.commit_documents") is docs_commit.commit_documents
```

and append this new test after `test_the_engine_can_bind_mark_validated_out_of_the_subtask_context`:

```python
def test_the_engine_can_bind_the_docs_commit_step_out_of_the_subtask_context() -> None:
    """The phase carries no `args:`, so all four parameters have to come from the
    context by name -- `card_details` and `worktree` from
    `engine.subtask_context`, `spec_path` and `plan_path` from
    `engine._document_paths`. `git_runner` has a default and must NOT be bound
    out of a context that happens to hold no such key."""
    card = models.Card(
        id="6f1a2f2e-1f1c-4f0e-9a6d-0c2f3b4a5d6e",
        title="Commit the spec and plan with the Plan-Hash trailer",
        status="todo",
    )
    bound = bind_arguments(
        docs_commit.commit_documents,
        {
            "card": "ba15da20",
            "card_details": card,
            "worktree": Path("/repo/.claude/worktrees/m2/task-docs-ba15da20"),
            "plan_path": "docs/superpowers/plans/task-docs-ba15da20.md",
            "spec_path": "docs/superpowers/specs/task-docs-ba15da20.md",
            "commands": ["uv run pytest"],
        },
        None,
        phase="docs_commit",
        function="docs_commit.commit_documents",
    )

    assert bound == {
        "card_details": card,
        "spec_path": "docs/superpowers/specs/task-docs-ba15da20.md",
        "plan_path": "docs/superpowers/plans/task-docs-ba15da20.md",
        "worktree": Path("/repo/.claude/worktrees/m2/task-docs-ba15da20"),
    }
```

That test needs `models`, so extend the imports at the top of `tests/workflow/test_registry.py`:

```python
from agent_manager import models
```

In `tests/workflow/test_builtin_task.py`, add the phase to `EXPECTED_PHASES` (line 43) directly after `("mark_validated", "deterministic")`:

```python
    ("mark_validated", "deterministic"),
    ("docs_commit", "deterministic"),
    ("implement", "agent"),
```

rename the count test (line 67) to match its new arity:

```python
def test_builtin_task_has_the_fourteen_phases_in_spec_order() -> None:
```

append this new document test after `test_mark_validated_stamps_the_plan_between_validation_and_implement`:

```python
def test_docs_commit_sits_between_the_marker_and_the_coder() -> None:
    """The hash this phase stamps is the hash of the plan file WITH the
    validated marker on it, which is the hash `review` recomputes -- so it has
    to run after `mark_validated`. It must also run before `implement`, or the
    coder's own `git add` would sweep the documents into its commit and the
    docs commit would never exist."""
    workflow = load_builtin("task")
    phase = workflow.phase("docs_commit")
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "docs_commit.commit_documents"
    # No args: `bind_arguments` takes card_details, spec_path, plan_path and
    # worktree from the context by parameter name. Not best-effort and not
    # gated: an uncommitted or untagged pair of documents must escalate.
    assert phase.args == {}
    assert phase.gates == []
    assert phase.best_effort is False
    assert phase.when is None
    assert phase.skip_to is None

    names = workflow.phase_names
    assert names.index("mark_validated") < names.index("docs_commit")
    assert names.index("docs_commit") < names.index("implement")
```

and add the new phase's result to `_phase_results` (line 361), after the `validate_plan` entry, so the binding table this file builds matches a real run:

```python
        "mark_validated": {"path": PLAN_PATH, "appended": True},
        "docs_commit": {"plan_hash": PLAN_HASH},
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow -v`
Expected: FAIL — the retargeted `skip_to` assertion, `test_default_registry_holds_exactly_the_names_task_yaml_uses` (the name is not registered), `test_builtin_task_has_the_fourteen_phases_in_spec_order`, `test_docs_commit_sits_between_the_marker_and_the_coder` (`workflow.phase("docs_commit")` raises), and `test_every_resolved_function_is_the_registry_binding`.

- [ ] **Step 3: Register the callable**

In `src/agent_manager/workflow/registry.py`, extend the steps import on line 19:

```python
from agent_manager.steps import docs_commit, plan_check, reducers, rollup, verify, worktree
```

add the name to `BUILTIN_FUNCTION_NAMES` in sorted position (between `"critic_blockers_gate"` and `"exploration_output_gate"`):

```python
    "critic_blockers_gate",
    "docs_commit.commit_documents",
    "exploration_output_gate",
```

and register the real callable in `default_registry()`, in the deterministic-steps block after the `plan_check.mark_validated` line:

```python
    registry.register("docs_commit.commit_documents", docs_commit.commit_documents)
```

- [ ] **Step 4: Add the phase to the document**

In `src/agent_manager/workflow/builtin/task.yaml`, insert between the `mark_validated` phase (ending line 59) and the `implement` phase (starting line 61):

```yaml
  - name: docs_commit
    kind: deterministic
    run: docs_commit.commit_documents
```

Then retarget the resume jump: in the `plan_check` phase change `skip_to: implement` to `skip_to: docs_commit`. Reason (verified: `engine` jumps straight to the named phase): with `skip_to: implement`, a resumed run that finds a validated plan would skip `docs_commit`, so (a) design §9's idempotent re-run path this card builds would be unreachable, (b) a kill between `mark_validated` and `docs_commit` would leave the documents uncommitted for the coder's `add -A`, and (c) `context["docs_commit"]["plan_hash"]`, which sibling `f26b377d` reads, would be absent on every resumed run. The loader still accepts it (forward jump).

In `tests/workflow/test_builtin_task.py`, update `test_plan_check_skips_forward_to_implement_when_a_plan_exists` (line 91): rename it `..._to_docs_commit_...` and assert `phase.skip_to == "docs_commit"` (line 96).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/workflow/registry.py src/agent_manager/workflow/builtin/task.yaml tests/workflow/test_registry.py tests/workflow/test_builtin_task.py
git commit -m "feat(workflow): run docs_commit between mark_validated and implement"
```

---

### Task 6: Prove the engine authors the docs commit under a fake claude

**Files:**
- Modify: `tests/e2e/fake_claude.py:260-272`
- Modify: `tests/e2e/test_production_wiring.py:19-27` (phase list is unchanged; the new test goes at the end of the file)
- Test: `tests/e2e/test_production_wiring.py`

**Interfaces:**
- Consumes: the registered `docs_commit` phase (Task 5); `docs_commit.SUBJECT_TEMPLATE` and `docs_commit.TRAILER_PREFIX` (Task 2) — the test imports them rather than retyping the strings, so a wording change fails in one place.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Write the failing test**

Append to `tests/e2e/test_production_wiring.py`:

```python
def test_the_engine_authored_the_docs_commit_before_the_coder_ran(
    project, completed_run, worktree
):
    """R4: the fake harness knows no more than its brief and does no work the
    engine owes. The spec and the plan are committed by the `docs_commit` step,
    with the Plan-Hash trailer, before `implement` ever starts -- so the docs
    commit is OLDER than the fake's implementation commit."""
    workflow = load_builtin("task")
    card = board.show(completed_run["card_id"], repo_dir=project)
    plan_relative = prompt.expand_writes(
        workflow.phase("plan").writes, card, phase="plan", input_name="plan_path"
    )
    spec_relative = prompt.expand_writes(
        workflow.phase("spec").writes, card, phase="spec", input_name="spec_path"
    )
    expected_hash = hashlib.sha256(
        (worktree / plan_relative).read_bytes()
    ).hexdigest()[:8]
    subject = docs_commit.SUBJECT_TEMPLATE.format(title=card.title)

    # `rev-list` is newest-first, so the docs commit must come LAST.
    revisions = _git(worktree, "rev-list", "main..HEAD").split()
    subjects = [
        _git(worktree, "show", "-s", "--format=%s", revision).strip()
        for revision in revisions
    ]
    assert subject in subjects, subjects
    assert subjects.index(subject) == len(subjects) - 1, subjects

    docs_revision = revisions[subjects.index(subject)]
    message = _git(worktree, "show", "-s", "--format=%B", docs_revision).rstrip("\n")
    assert message.splitlines()[-1] == f"{docs_commit.TRAILER_PREFIX}{expected_hash}"

    named = _git(
        worktree, "show", "--name-only", "--format=", docs_revision
    ).split()
    assert sorted(named) == sorted([spec_relative, plan_relative])
```

and extend that module's imports (line 16) so it can reach the step's constants:

```python
from agent_manager import board, prompt, results
from agent_manager.steps import docs_commit
```

- [ ] **Step 2: Run the whole e2e module to verify it fails**

Run: `uv run pytest tests/e2e/test_production_wiring.py -v`
Expected: FAIL — and the failure is wider than the new test. Every test in the module now errors in the `completed_run` fixture, because the `docs_commit` phase committed the spec and the plan, so the fake's `implement` branch runs `git add -A` with nothing staged and `git commit` exits non-zero (`FakeClaudeError: git commit failed`). That is precisely the breakage spec §4 says to adjust.

- [ ] **Step 3: Give the fake coder something of its own to commit**

In `tests/e2e/fake_claude.py`, add a constant next to `LOG_NAME`:

```python
IMPLEMENTATION_NAME = "IMPLEMENTATION.md"
"""The one file the fake coder writes, so its commit is not empty.

The engine's `docs_commit` phase now commits the spec and the plan before
`implement` runs (card ba15da20), which is the whole point: the fake must not
do work the engine owes. A coder that wrote nothing at all would then have an
empty `git commit` and fail, so this fake writes the one file a real coder
would have written.
"""
```

and replace the `implement` branch of `build_result` (lines 260-272) with:

```python
    if phase == "implement":
        relative = _section(found, "plan_path", phase)
        digest = plan_hash_of(Path(cwd) / relative)
        (Path(cwd) / IMPLEMENTATION_NAME).write_text(
            f"# implementation\n\n{SUMMARY}\n", encoding="utf-8"
        )
        git(cwd, "add", "-A")
        git(cwd, "commit", "-m", f"feat: implement this card\n\nPlan-Hash: {digest}")
        return override(
            payload,
            blocked=False,
            blocked_reason=None,
            resumed=False,
            plan_hash=digest,
            report=SUMMARY,
        )
```

- [ ] **Step 4: Run the e2e module to verify it passes**

Run: `uv run pytest tests/e2e/test_production_wiring.py -v`
Expected: PASS. In particular:
- `test_the_worktree_is_clean_after_the_run` (spec test 12) still passes — the docs commit plus the fake's `add -A` leave nothing behind.
- `test_the_spec_and_plan_documents_exist_where_the_phases_declared_them` still passes — both are tracked, now by the engine's commit.
- `test_the_implement_commit_carries_a_plan_hash_trailer_review_agrees_with` still passes — `review["commit_count"]` is `len(revisions)` counted the same way on both sides, and every revision (docs commit included) carries a trailer, so `tagged_count == commit_count` holds with the extra commit.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, with no new skips and no new markers.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/fake_claude.py tests/e2e/test_production_wiring.py
git commit -m "test(e2e): assert the engine, not the fake, authors the docs commit"
```
