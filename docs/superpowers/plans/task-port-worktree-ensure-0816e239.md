<!-- task-pipeline: validated -->
# Spec (verbatim)

> The spec below is reproduced verbatim from `docs/superpowers/specs/task-port-worktree-ensure-0816e239-design.md`. The plan follows it.

---

# Port `worktree.ensure` — subtask design (card 0816e239)

Narrows the milestone design (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`, the source of truth per `CLAUDE.md`) to one deterministic step module. Ports the `prepare` logic of the legacy `scripts/worktree.mjs` (`/home/paulomtts/.claude/plugins/cache/paulomtts-plugins/leave-me-alone/4.1.2/scripts/worktree.mjs`) to Python; its `worktree.test.mjs` is the behavioural specification.

## Scope

One module, `src/agent_manager/steps/worktree.py`, listed in §4 of the design under `steps/` (no model calls). It exposes a single public function, `ensure(...)`, taking the explicit arguments listed under Observable behaviour below and returning a `dict`. Its return shape conforms to what the §6 deterministic-phase contract requires of a phase result. **Out of scope for this card**: the `run(ctx) -> dict` engine entry point itself, and any `workflow/registry.py` wiring that maps a phase name to `ensure` — those belong to whichever future card builds the engine/registry (neither exists yet in this milestone) and must adapt a `ctx` mapping into `ensure`'s explicit arguments; this card defines and tests only `ensure`. Per `CLAUDE.md`, Pydantic is required only at process boundaries (harness result files); this is an internal deterministic result, so a plain dict (optionally backed by a dataclass) is correct.

Inputs are already resolved by the caller: `branch` and `base_branch` are computed by `dag.py` and inlined (§7). The module never derives a branch name, never reads the board, and never touches plan or verification concerns.

**Out of scope** (owned by siblings or later milestones): plan-file discovery and the `validated` marker (`steps/plan_check.py`, card d3feb87e); running verification commands in the worktree (`steps/verify.py`, card 9c3b1ffb); census, levels, parallel stories, integrate, non-Claude harnesses. No CLI command is added by this card.

## Observable behaviour

`ensure` takes the subtask `branch`, the `base` branch name, the absolute `worktree` path and the absolute `repo_dir`, plus an injectable git runner, and returns snake_case keys (the JS camelCase does not carry over):

- `branch`, `worktree` — echoed back.
- `branch_existed` — `branch` appears in `git -C <repo_dir> for-each-ref --format=%(refname:short) refs/heads/`, matched as an **exact** line, never a prefix: `m1/task-` must not match `m1/task-9`.
- `worktree_existed` — `worktree` appears among the paths parsed from `git -C <repo_dir> worktree list --porcelain`, taking only lines beginning with the literal `worktree ` prefix and stripping it.
- `created` — true exactly when this call ran `git worktree add`.
- `commit_count` — `git -C <worktree> rev-list --count <resolved_base>..HEAD`, parsed as an int, `0` on any parse failure.

Base resolution: try `git -C <repo_dir> rev-parse --verify --quiet origin/<base>`; on success use `origin/<base>`, on failure fall back to the bare local `<base>`. Rationale from the source: every base other than the milestone's own base branch is a local branch this run created and never pushed, so most resolutions legitimately fall back.

Creation decision:

- worktree already exists → **no** `git worktree add` call at all; the existing directory is left untouched and `created` is false.
- worktree missing, branch existed → `git -C <repo_dir> worktree add <worktree> <branch>` (checkout, never re-cut: re-cutting would silently discard a killed run's prior commits).
- worktree missing, branch new → `git -C <repo_dir> worktree add <worktree> -b <branch> <resolved_base>`.

`commit_count` is computed in both the created and the already-existed paths.

**Forbidden operations**, asserted by tests: no `reset`, no `checkout -f`, no `clean`, no `commit`, no `push`, no `worktree remove`, no `prune`, no deletion of any path. Deciding resume vs reset needs a plan hash that does not exist at this point in the run; that decision is an explicit non-goal.

## Error paths

- Missing or empty `branch`/`base`, or a non-absolute `worktree`/`repo_dir` → raise a validation error before any git call (the legacy `parseArgs` contract, carried into the function signature rather than a CLI flag parser).
- `origin/<base>` not resolving is **not** an error — it is the expected fallback.
- A failing `git worktree add` propagates; the module does not attempt cleanup or retry.
- Non-numeric or empty `rev-list --count` output → `commit_count = 0`, no raise.
- An absent `.git`/invalid `repo_dir` surfaces as the underlying git failure; no special-casing.

## Test list

Placement rule (§14): `worktree.py` is a **Steps** component, so its tests are the *steps tier* — "against temporary git repositories and a temporary `brd` board; no network". The ported `.mjs` tests used a fake git callable; that strategy is adapted: these run real `git init` / `git worktree` in `tmp_path`. Tests live at `tests/steps/test_worktree.py`, mirroring the source path per `CLAUDE.md`. No integration or end-to-end tier tests are added by this card.

Steps tier, real temporary git repos:

1. Fresh repo, new branch, missing worktree — worktree dir exists afterwards; `branch_existed` false, `worktree_existed` false, `created` true, `commit_count` 0.
2. Second call with identical arguments is idempotent — `branch_existed` true, `worktree_existed` true, `created` false, and the worktree directory and its HEAD are unchanged.
3. Branch exists but worktree missing — checkout path; a commit made earlier on that branch survives and is reflected in `commit_count`, proving the branch was not re-cut from base.
4. Existing worktree with uncommitted local changes — files on disk are byte-identical after the call (no reset/clean).
5. `commit_count` counts only commits on top of the resolved base; a repo with N subtask commits reports N.
6. Exact branch matching — a repo containing `m1/task-9` asked for `m1/task-` reports `branch_existed` false and creates a new branch.
7. Porcelain worktree-path matching — a repo with sibling worktrees reports `worktree_existed` only for an exact path match, and ignores non-`worktree ` porcelain lines (e.g. `branch refs/heads/...`).
8. Base fallback — a repo with no `origin` remote resolves to the bare local base and succeeds; a repo with an `origin` whose `origin/<base>` resolves uses `origin/<base>` (assert via the branch's merge-base/commit, and/or the recorded git argv).
9. Forbidden-operation guard — with a recording git runner wrapping the real one, assert no invocation contains `reset`, `checkout -f`, `clean`, `commit`, `push`, `worktree remove`, or `prune`, across the create path, the resume path, and the already-exists path.
10. Argument validation — empty branch, empty base, relative `worktree`, relative `repo_dir` each raise before any git invocation is recorded.

Verify with `uv run pytest`; the repo has no separate lint or typecheck command.

---

# Port `worktree.ensure` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/agent_manager/steps/worktree.py` with a single public `ensure(...)` that idempotently creates a subtask's git worktree and reports what was already there, never resetting, deleting or committing.

**Architecture:** One deterministic step module (design §4 `steps/`) with no model calls and no board access. Git is reached through an injectable `git_runner(argv) -> str` callable whose default, `run_git`, shells out with `subprocess` using argument lists (design §5: argument lists, never shell strings), raising `GitError` on a non-zero exit — the same adapter shape `board.py` already uses for `brd`. `ensure` is a straight port of `prepare` from the legacy `scripts/worktree.mjs`: probe branch, probe worktree, resolve base, conditionally `git worktree add`, count commits.

**Tech Stack:** Python 3.12+, stdlib `subprocess`/`pathlib`/`os`, pytest (dev dependency), `uv` for running. No Pydantic here — `ensure`'s dict is an internal deterministic-phase result, not a process-boundary payload (`CLAUDE.md`).

**Spec:** `docs/superpowers/specs/task-port-worktree-ensure-0816e239-design.md` (reproduced verbatim above)

## Global Constraints

- Source lives at `src/agent_manager/steps/worktree.py`; its tests mirror it at `tests/steps/test_worktree.py` (`CLAUDE.md`; the steps tier already has `tests/steps/test_reducers.py` and no `__init__.py`, so follow that layout — no new `conftest.py`).
- Tests are the **steps tier** (design §14): real temporary git repositories via `git init` / `git worktree` in `tmp_path`, no network, no mocked git except where a test must force an unparseable git output.
- Verification command for the whole repo: `uv run pytest`. There is no separate lint or typecheck command.
- Public result keys are exactly `branch`, `worktree`, `branch_existed`, `worktree_existed`, `created`, `commit_count` — snake_case, never the JS camelCase.
- Forbidden in the module's own git invocations, for all time: `reset`, `checkout -f`, `clean`, `commit`, `push`, `worktree remove`, `prune`, and any path deletion.
- No CLI command, no engine `run(ctx)` wiring, no plan-file discovery, no verification-command execution is added by this card.
- Branch for this work: `m1/task-port-worktree-ensure-0816e239`. Nothing from sibling cards (`steps/plan_check.py`, `steps/verify.py`) exists on it.

## Review Focus

- A `worktree` path that is non-normalized relative to what git recorded (trailing slash, a `.` component, a symlinked parent) must still be recognised as the existing worktree — otherwise `ensure` calls `git worktree add` onto a live directory and the run dies on a resume. Pinned in Task 3.
- A directory that already exists on disk at `worktree` but is **not** a registered worktree — `git worktree add` fails and that failure must propagate verbatim, never be swallowed into `created: false`. Pinned in Task 2.
- `rev-list --count` printing something non-numeric (empty output, an error line on stdout) must yield `commit_count == 0`, not a `ValueError` that aborts a worktree that was successfully created. Pinned in Task 4.
- `for-each-ref` output with blank or whitespace-padded lines must not produce a phantom branch match (an empty-string ref matching an empty branch, or `" m1/task-9 "` failing to match `m1/task-9`). Pinned in Task 1.
- A whitespace-only `branch` or `base` (`"   "`) is as unusable as an empty one and must be rejected before any git call — the legacy JS only checked `length > 0` and would have shelled out with it. Pinned in Task 2.

---

### Task 1: Git runner and the two output parsers

**Files:**
- Create: `src/agent_manager/steps/worktree.py`
- Create: `tests/steps/test_worktree.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `GitRunner = Callable[[list[str]], str]`; `class GitError(RuntimeError)` with `.message: str`, `.argv: list[str]`, `.exit_code: int | None`; `run_git(argv: list[str]) -> str`; `worktree_paths(porcelain: str) -> list[str]`; `branch_exists(ref_list: str, branch: str) -> bool`.

- [ ] **Step 1: Write the failing tests for the parsers and the default runner**

Create `tests/steps/test_worktree.py` with exactly this content:

```python
"""Behaviour of the subtask worktree step (design §4 `steps/`, spec card 0816e239).

Placement follows design §14: `worktree.py` is a Steps component, so its
behaviour is exercised against real temporary git repositories created with
`git init` / `git worktree` in `tmp_path` -- no network, and no faking of git
except where a test must force an output git itself would never print.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_manager.steps import worktree
from agent_manager.steps.worktree import GitError

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the worktree step's steps-tier tests",
)


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in `cwd` for test setup, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def _head(cwd: Path) -> str:
    return _git(cwd, "rev-parse", "HEAD").strip()


def _commit(cwd: Path, name: str, body: str) -> str:
    (Path(cwd) / name).write_text(body)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-m", f"add {name}")
    return _head(cwd)


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
    _commit(root, "README.md", "base\n")
    return root


def _recorder(calls: list[list[str]], inner=None):
    """A git runner that records every argv, optionally delegating to `inner`."""

    def runner(argv: list[str]) -> str:
        calls.append(list(argv))
        return "" if inner is None else inner(argv)

    return runner


def test_worktree_paths_takes_only_the_worktree_lines():
    porcelain = (
        "worktree /abs/repo\n"
        "HEAD 1111111111111111111111111111111111111111\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /abs/wt\n"
        "HEAD 2222222222222222222222222222222222222222\n"
        "branch refs/heads/m1/task-9\n"
    )
    assert worktree.worktree_paths(porcelain) == ["/abs/repo", "/abs/wt"]


def test_worktree_paths_of_empty_output_is_empty():
    assert worktree.worktree_paths("") == []


def test_branch_exists_matches_a_whole_line_never_a_prefix():
    refs = "main\nm1/task-9\n"
    assert worktree.branch_exists(refs, "m1/task-9") is True
    # The prefix must NOT match: this is the bug the JS test guarded.
    assert worktree.branch_exists(refs, "m1/task-") is False


def test_branch_exists_ignores_blank_and_padded_lines():
    # A blank line must not become an empty ref that matches an empty branch,
    # and padding must not stop a real ref from matching.
    assert worktree.branch_exists("main\n\n  m1/task-9  \n", "m1/task-9") is True
    assert worktree.branch_exists("main\n\n\n", "") is False


@requires_git
def test_run_git_returns_stdout(repo: Path):
    out = worktree.run_git(["-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"])
    assert out.strip() == "main"


@requires_git
def test_run_git_raises_git_error_carrying_argv_and_exit_code(tmp_path: Path):
    argv = ["-C", str(tmp_path), "rev-parse", "--verify", "origin/nope"]
    with pytest.raises(GitError) as excinfo:
        worktree.run_git(argv)
    assert excinfo.value.argv == argv
    assert excinfo.value.exit_code not in (None, 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_worktree.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'agent_manager.steps.worktree'`.

- [ ] **Step 3: Write the module with the runner and the parsers**

Create `src/agent_manager/steps/worktree.py`:

```python
"""Create a subtask's worktree, idempotently, and report what was already there.

A deterministic step (design §4 `steps/`, §6): no model calls, no board access,
no network beyond whatever git itself does. Ported from `prepare` in the
leave-me-alone plugin's `scripts/worktree.mjs`, whose `worktree.test.mjs` is the
behavioural specification.

It creates and reports. It never resets, never deletes, never commits: deciding
RESUME vs RESET needs the plan hash, which does not exist at this point in the
run, so the forbidden-operations list (`reset`, `checkout -f`, `clean`,
`commit`, `push`, `worktree remove`, `prune`) is asserted by the tests.

Every invocation is an argument list handed to `subprocess` (design §5 line
252): there is no shell string and nothing to quote.
"""

import subprocess
from collections.abc import Callable

GIT = "git"
"""Executable name, resolved on PATH. Argv element zero of every call."""

GitRunner = Callable[[list[str]], str]
"""Takes a git argv (without the leading `git`), returns stdout, raises on failure."""


class GitError(RuntimeError):
    """A git invocation that exited non-zero, carrying enough to journal it."""

    def __init__(
        self,
        message: str,
        *,
        argv: list[str],
        exit_code: int | None = None,
    ) -> None:
        self.message = message
        self.argv = list(argv)
        self.exit_code = exit_code
        super().__init__(f"{message} (argv={self.argv!r}, exit_code={exit_code!r})")


def run_git(argv: list[str]) -> str:
    """The default `GitRunner`: run `git <argv>` and return its stdout."""
    try:
        completed = subprocess.run(
            [GIT, *argv],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise GitError(f"could not run {GIT}: {exc.strerror}", argv=argv) from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise GitError(detail, argv=argv, exit_code=completed.returncode)

    return completed.stdout


_WORKTREE_PREFIX = "worktree "


def worktree_paths(porcelain: str) -> list[str]:
    """The paths from `git worktree list --porcelain`.

    Only lines starting with the literal `worktree ` are entries; the
    `HEAD <sha>`, `branch refs/heads/...` and blank lines between records are
    not paths and must not be read as one.
    """
    return [
        line[len(_WORKTREE_PREFIX) :].strip()
        for line in porcelain.split("\n")
        if line.startswith(_WORKTREE_PREFIX)
    ]


def branch_exists(ref_list: str, branch: str) -> bool:
    """Whether `branch` is one whole line of a `%(refname:short)` listing.

    Exact line equality, never a prefix test: `m1/task-` must not match
    `m1/task-9`, or a resumed run would check out the wrong branch. Blank lines
    are dropped so an empty `branch` can never match one.
    """
    refs = [line.strip() for line in ref_list.split("\n") if line.strip()]
    return branch in refs
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_worktree.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/worktree.py tests/steps/test_worktree.py
git commit -m "feat(steps): add git runner and worktree output parsers"
```

---

### Task 2: `ensure` — argument validation and the create path

**Files:**
- Modify: `src/agent_manager/steps/worktree.py`
- Modify: `tests/steps/test_worktree.py`

**Interfaces:**
- Consumes: `run_git`, `GitError`, `GitRunner`, `worktree_paths`, `branch_exists` from Task 1.
- Produces: `ensure(branch: str, base: str, worktree: str | Path, repo_dir: str | Path, git_runner: GitRunner = run_git) -> dict[str, object]` returning keys `branch: str`, `worktree: str`, `branch_existed: bool`, `worktree_existed: bool`, `created: bool`, `commit_count: int`. Raises `ValueError` for bad arguments, before any git call.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_worktree.py`:

```python
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"branch": ""}, "branch"),
        ({"branch": "   "}, "branch"),
        ({"base": ""}, "base"),
        ({"base": "   "}, "base"),
        ({"worktree": "relative/wt"}, "worktree"),
        ({"worktree": ""}, "worktree"),
        ({"repo_dir": "relative/repo"}, "repo_dir"),
        ({"repo_dir": ""}, "repo_dir"),
    ],
    ids=[
        "empty-branch",
        "blank-branch",
        "empty-base",
        "blank-base",
        "relative-worktree",
        "empty-worktree",
        "relative-repo-dir",
        "empty-repo-dir",
    ],
)
def test_bad_arguments_raise_before_any_git_invocation(kwargs, expected):
    calls: list[list[str]] = []
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": "/abs/wt",
        "repo_dir": "/abs/repo",
        **kwargs,
    }
    with pytest.raises(ValueError, match=expected):
        worktree.ensure(**args, git_runner=_recorder(calls))
    assert calls == []


@requires_git
def test_a_fresh_branch_and_missing_worktree_is_created(repo: Path, tmp_path: Path):
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
    )

    assert wt.is_dir()
    assert result == {
        "branch": "m1/task-9",
        "worktree": str(wt),
        "branch_existed": False,
        "worktree_existed": False,
        "created": True,
        "commit_count": 0,
    }
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "m1/task-9"


@requires_git
def test_a_directory_that_is_not_a_registered_worktree_propagates_gits_failure(
    repo: Path, tmp_path: Path
):
    # An existing directory that git does not know about is NOT "already
    # prepared": `worktree add` refuses it, and that refusal must surface
    # rather than be reported as a quiet no-op.
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "stray.txt").write_text("not a worktree\n")

    with pytest.raises(GitError) as excinfo:
        worktree.ensure(
            branch="m1/task-9",
            base="main",
            worktree=str(wt),
            repo_dir=str(repo),
        )

    assert "worktree" in excinfo.value.argv
    assert "add" in excinfo.value.argv


@requires_git
def test_an_exact_branch_match_is_required_before_checking_out(
    repo: Path, tmp_path: Path
):
    # `m1/task-9` exists; `m1/task-` is a different branch and must be cut new.
    _git(repo, "branch", "m1/task-9")
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
    )

    assert result["branch_existed"] is False
    assert result["created"] is True
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "m1/task-"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_worktree.py -v`
Expected: the 11 new tests FAIL with `AttributeError: module 'agent_manager.steps.worktree' has no attribute 'ensure'`.

- [ ] **Step 3: Implement validation and the create path**

Add to the imports at the top of `src/agent_manager/steps/worktree.py` (only `Path` is new; `subprocess` and `Callable` are already imported by Task 1, do not duplicate them):

```python
from pathlib import Path
```

Append to `src/agent_manager/steps/worktree.py`:

```python
def _required_name(value: object, field: str) -> str:
    """A non-blank ref name, or `ValueError` before anything is run."""
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(
            f"worktree.ensure needs a non-empty {field} name, got {value!r}"
        )
    return value


def _required_absolute(value: object, field: str) -> str:
    """An absolute path as a string, or `ValueError` before anything is run."""
    if not isinstance(value, (str, Path)):
        raise ValueError(
            f"worktree.ensure needs an absolute path for {field}, got {value!r}"
        )
    text = str(value).strip()
    if text == "" or not Path(text).is_absolute():
        raise ValueError(
            f"worktree.ensure needs an absolute path for {field}, got {value!r}"
        )
    return text


def ensure(
    branch: str,
    base: str,
    worktree: str | Path,
    repo_dir: str | Path,
    git_runner: GitRunner = run_git,
) -> dict[str, object]:
    """Make sure `branch`'s worktree exists at `worktree`, and report what was there.

    `branch` and `base` arrive already computed by `dag.py` and inlined
    (design §7); this module never derives a branch name. The return value is
    the deterministic phase's result (design §6) -- a plain dict, since it
    crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`).
    """
    branch = _required_name(branch, "branch")
    base = _required_name(base, "base")
    worktree_path = _required_absolute(worktree, "worktree")
    repo_path = _required_absolute(repo_dir, "repo_dir")

    branch_existed = branch_exists(
        git_runner(
            [
                "-C",
                repo_path,
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads/",
            ]
        ),
        branch,
    )
    registered = worktree_paths(
        git_runner(["-C", repo_path, "worktree", "list", "--porcelain"])
    )
    worktree_existed = _is_registered(worktree_path, registered)

    resolved_base = base

    created = False
    if not worktree_existed:
        if branch_existed:
            # Check the existing branch out. Never re-cut it from base: a
            # killed run's commits live on that branch and re-cutting would
            # silently discard them.
            argv = ["-C", repo_path, "worktree", "add", worktree_path, branch]
        else:
            argv = [
                "-C",
                repo_path,
                "worktree",
                "add",
                worktree_path,
                "-b",
                branch,
                resolved_base,
            ]
        git_runner(argv)
        created = True

    return {
        "branch": branch,
        "worktree": worktree_path,
        "branch_existed": branch_existed,
        "worktree_existed": worktree_existed,
        "created": created,
        "commit_count": 0,
    }
```

And add the path-comparison helper above `ensure`:

```python
def _is_registered(candidate: str, registered: list[str]) -> bool:
    """Whether `candidate` is one of the paths git already has registered."""
    return candidate in registered
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_worktree.py -v`
Expected: PASS (17 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/worktree.py tests/steps/test_worktree.py
git commit -m "feat(steps): add worktree.ensure validation and create path"
```

---

### Task 3: Idempotence — an existing worktree is left completely alone

**Files:**
- Modify: `tests/steps/test_worktree.py`
- Modify: `src/agent_manager/steps/worktree.py` (only if the tests demand it)

**Interfaces:**
- Consumes: `ensure` and `_is_registered(candidate: str, registered: list[str]) -> bool` from Task 2.
- Produces: no new public names; `_is_registered` gains a `realpath` fallback, and the `worktree_existed → created is False, no worktree add` behaviour is pinned.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_worktree.py`:

```python
@requires_git
def test_a_second_identical_call_is_a_no_op(repo: Path, tmp_path: Path):
    wt = tmp_path / "wt"
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": str(wt),
        "repo_dir": str(repo),
    }
    worktree.ensure(**args)
    head_before = _head(wt)

    result = worktree.ensure(**args)

    assert result["branch_existed"] is True
    assert result["worktree_existed"] is True
    assert result["created"] is False
    assert _head(wt) == head_before


@requires_git
def test_an_existing_worktree_add_is_never_attempted_a_second_time(
    repo: Path, tmp_path: Path
):
    wt = tmp_path / "wt"
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": str(wt),
        "repo_dir": str(repo),
    }
    worktree.ensure(**args)

    calls: list[list[str]] = []
    worktree.ensure(**args, git_runner=_recorder(calls, worktree.run_git))

    assert not any("add" in argv for argv in calls)


@requires_git
def test_uncommitted_local_changes_survive_untouched(repo: Path, tmp_path: Path):
    wt = tmp_path / "wt"
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": str(wt),
        "repo_dir": str(repo),
    }
    worktree.ensure(**args)
    (wt / "README.md").write_text("edited by a killed run\n")
    (wt / "scratch.txt").write_text("untracked work in progress\n")
    status_before = _git(wt, "status", "--porcelain")

    worktree.ensure(**args)

    assert (wt / "README.md").read_text() == "edited by a killed run\n"
    assert (wt / "scratch.txt").read_text() == "untracked work in progress\n"
    assert _git(wt, "status", "--porcelain") == status_before


@requires_git
def test_a_non_normalized_worktree_path_still_counts_as_existing(
    repo: Path, tmp_path: Path
):
    # The caller's string and git's recorded path routinely differ by a
    # trailing slash or a `.` component. Treating those as a missing worktree
    # would send `worktree add` at a live directory and kill the run.
    wt = tmp_path / "wt"
    worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
    )

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=f"{wt}{os.sep}.{os.sep}",
        repo_dir=str(repo),
    )

    assert result["worktree_existed"] is True
    assert result["created"] is False


@requires_git
def test_a_sibling_worktree_at_another_path_does_not_count_as_this_one(
    repo: Path, tmp_path: Path
):
    sibling = tmp_path / "sibling-wt"
    _git(repo, "worktree", "add", str(sibling), "-b", "m1/task-8")
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
    )

    assert result["worktree_existed"] is False
    assert result["created"] is True
    assert sibling.is_dir()
```

- [ ] **Step 2: Run the tests to verify one fails**

Run: `uv run pytest tests/steps/test_worktree.py -v`
Expected: `test_a_non_normalized_worktree_path_still_counts_as_existing` FAILS — Task 2's `_is_registered` is a plain string membership test, so `<wt>/./` is judged missing and `git worktree add` raises `GitError: fatal: '<wt>/./' already exists`. The other four tests in this step pass; they pin behaviour Task 2 shipped and guard it against later tasks.

- [ ] **Step 3: Give `_is_registered` a realpath fallback**

Add `import os` to the import block at the top of `src/agent_manager/steps/worktree.py`, then replace `_is_registered` in full:

```python
def _is_registered(candidate: str, registered: list[str]) -> bool:
    """Whether `candidate` is one of the paths git already has registered.

    A plain string match first, as the JS did. Falling back to `realpath`
    matters because the caller's path and git's recorded path can differ by a
    trailing slash, a `.` component or a symlinked parent -- and a false
    negative here would run `worktree add` onto a live directory and abort the
    run.
    """
    if candidate in registered:
        return True
    real = os.path.realpath(candidate)
    return any(os.path.realpath(path) == real for path in registered)
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, with 22 tests in `tests/steps/test_worktree.py`.

- [ ] **Step 5: Commit**

```bash
git add tests/steps/test_worktree.py src/agent_manager/steps/worktree.py
git commit -m "fix(steps): match a registered worktree through a non-normalized path"
```

---

### Task 4: `commit_count` and the never-re-cut checkout path

**Files:**
- Modify: `src/agent_manager/steps/worktree.py`
- Modify: `tests/steps/test_worktree.py`

**Interfaces:**
- Consumes: `ensure`, `GitRunner`, `run_git` as defined in Tasks 1-2.
- Produces: `commit_count: int` in `ensure`'s result — `git -C <worktree> rev-list --count <resolved_base>..HEAD`, `0` on any parse failure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_worktree.py`:

```python
@requires_git
def test_an_existing_branchs_commits_survive_the_checkout_path(
    repo: Path, tmp_path: Path
):
    # A killed run left a commit on the subtask branch and no worktree. Cutting
    # the branch again from base would silently discard that commit, so this
    # call must check the branch out instead.
    staging = tmp_path / "staging-wt"
    _git(repo, "worktree", "add", str(staging), "-b", "m1/task-9")
    _commit(staging, "prior.txt", "work from a killed run\n")
    prior_head = _head(staging)
    _git(repo, "worktree", "remove", str(staging))
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
    )

    assert result["branch_existed"] is True
    assert result["created"] is True
    assert result["commit_count"] == 1
    assert _head(wt) == prior_head
    assert (wt / "prior.txt").read_text() == "work from a killed run\n"


@requires_git
def test_commit_count_counts_only_the_commits_on_top_of_the_base(
    repo: Path, tmp_path: Path
):
    wt = tmp_path / "wt"
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": str(wt),
        "repo_dir": str(repo),
    }
    worktree.ensure(**args)
    _commit(wt, "one.txt", "1\n")
    _commit(wt, "two.txt", "2\n")

    result = worktree.ensure(**args)

    assert result["worktree_existed"] is True
    assert result["created"] is False
    assert result["commit_count"] == 2


@requires_git
def test_an_unparseable_commit_count_is_zero_rather_than_a_crash(
    repo: Path, tmp_path: Path
):
    # The worktree was created successfully; a count git could not print is no
    # reason to abort the phase.
    wt = tmp_path / "wt"

    def runner(argv: list[str]) -> str:
        if "rev-list" in argv:
            return "\n"
        return worktree.run_git(argv)

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
        git_runner=runner,
    )

    assert result["commit_count"] == 0
    assert result["created"] is True


@requires_git
def test_a_non_numeric_commit_count_is_zero_rather_than_a_crash(
    repo: Path, tmp_path: Path
):
    wt = tmp_path / "wt"

    def runner(argv: list[str]) -> str:
        if "rev-list" in argv:
            return "fatal: bad revision\n"
        return worktree.run_git(argv)

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
        git_runner=runner,
    )

    assert result["commit_count"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_worktree.py -v`
Expected: `test_an_existing_branchs_commits_survive_the_checkout_path` and `test_commit_count_counts_only_the_commits_on_top_of_the_base` FAIL with `assert 0 == 1` / `assert 0 == 2` (Task 2 hard-coded `commit_count` to 0).

- [ ] **Step 3: Compute the count**

Add this helper above `ensure` in `src/agent_manager/steps/worktree.py`:

```python
def _commit_count(git_runner: GitRunner, worktree_path: str, resolved_base: str) -> int:
    """Commits on HEAD that are not on `resolved_base`, or 0 if unreadable.

    Mirrors the JS `Number(...) || 0`: an empty or non-numeric answer is no
    reason to fail a worktree that was just prepared successfully.
    """
    raw = git_runner(
        ["-C", worktree_path, "rev-list", "--count", f"{resolved_base}..HEAD"]
    )
    try:
        return int(str(raw).strip())
    except ValueError:
        return 0
```

Then replace the hard-coded entry in `ensure`'s returned dict:

```python
        "commit_count": _commit_count(git_runner, worktree_path, resolved_base),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_worktree.py -v`
Expected: PASS (26 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/worktree.py tests/steps/test_worktree.py
git commit -m "feat(steps): report commit_count and never re-cut an existing branch"
```

---

### Task 5: Base resolution (`origin/<base>` with local fallback) and the forbidden-operations guard

**Files:**
- Modify: `src/agent_manager/steps/worktree.py`
- Modify: `tests/steps/test_worktree.py`

**Interfaces:**
- Consumes: `ensure`, `_commit_count`, `GitError` as defined in Tasks 1-4.
- Produces: no new public names; `ensure` now resolves its base ref before cutting a branch or counting commits.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_worktree.py`:

```python
@pytest.fixture
def repo_with_origin(repo: Path, tmp_path: Path) -> Path:
    """`repo`, with a local bare `origin` holding main -- no network involved."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "origin", "main")
    _git(repo, "fetch", "origin")
    return repo


@requires_git
def test_a_base_with_no_origin_falls_back_to_the_local_ref(
    repo: Path, tmp_path: Path
):
    # The common case: every base but the milestone's own is a local branch
    # this run created and never pushed.
    local_head = _head(repo)
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
    )

    assert result["created"] is True
    assert _head(wt) == local_head


@requires_git
def test_origin_is_preferred_over_the_local_ref_when_it_resolves(
    repo_with_origin: Path, tmp_path: Path
):
    origin_head = _git(repo_with_origin, "rev-parse", "origin/main").strip()
    # Local main now moves ahead of origin/main, so the two disagree.
    _commit(repo_with_origin, "local-only.txt", "not pushed\n")
    assert _head(repo_with_origin) != origin_head
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo_with_origin),
    )

    assert result["created"] is True
    assert _head(wt) == origin_head
    assert not (wt / "local-only.txt").exists()


@requires_git
def test_the_origin_probe_is_recorded_and_its_failure_is_not_an_error(
    repo: Path, tmp_path: Path
):
    calls: list[list[str]] = []
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
        git_runner=_recorder(calls, worktree.run_git),
    )

    assert ["rev-parse", "--verify", "--quiet", "origin/main"] == calls[2][2:]
    assert result["created"] is True


FORBIDDEN_TOKENS = ("reset", "clean", "commit", "push", "prune")


def _assert_no_forbidden_git(calls: list[list[str]]) -> None:
    for argv in calls:
        for token in FORBIDDEN_TOKENS:
            assert token not in argv, f"forbidden git operation {token!r} in {argv!r}"
        assert not ("checkout" in argv and "-f" in argv), argv
        assert not ("worktree" in argv and "remove" in argv), argv


@requires_git
def test_no_forbidden_git_operation_runs_on_any_path(repo: Path, tmp_path: Path):
    # Create path, resume-a-branch path and already-exists path, in one run.
    staging = tmp_path / "staging-wt"
    _git(repo, "worktree", "add", str(staging), "-b", "m1/task-8")
    _commit(staging, "prior.txt", "work from a killed run\n")
    _git(repo, "worktree", "remove", str(staging))

    calls: list[list[str]] = []
    runner = _recorder(calls, worktree.run_git)

    fresh = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(tmp_path / "wt-9"),
        repo_dir=str(repo),
        git_runner=runner,
    )
    resumed = worktree.ensure(
        branch="m1/task-8",
        base="main",
        worktree=str(tmp_path / "wt-8"),
        repo_dir=str(repo),
        git_runner=runner,
    )
    again = worktree.ensure(
        branch="m1/task-8",
        base="main",
        worktree=str(tmp_path / "wt-8"),
        repo_dir=str(repo),
        git_runner=runner,
    )

    assert fresh["created"] is True
    assert resumed["branch_existed"] is True
    assert again["created"] is False
    _assert_no_forbidden_git(calls)
    assert (tmp_path / "wt-8" / "prior.txt").is_file()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_worktree.py -v`
Expected: `test_origin_is_preferred_over_the_local_ref_when_it_resolves` FAILS (`_head(wt)` is local main, not `origin_head`) and `test_the_origin_probe_is_recorded_and_its_failure_is_not_an_error` FAILS with `IndexError: list index out of range` — `ensure` never probes `origin/<base>` yet.

- [ ] **Step 3: Resolve the base before using it**

In `src/agent_manager/steps/worktree.py`, replace the line `    resolved_base = base` inside `ensure` with:

```python
    resolved_base = _resolve_base(git_runner, repo_path, base)
```

and add this helper above `ensure`:

```python
def _resolve_base(git_runner: GitRunner, repo_path: str, base: str) -> str:
    """`origin/<base>` when it resolves, else the bare local `<base>`.

    `base` names a real remote branch only when it IS the milestone's own base
    branch -- every other base is another subtask's or story's local branch,
    which this run created and never pushes. A missing `origin/<base>` is the
    expected case, not an error, so the probe's failure is swallowed here and
    nowhere else.
    """
    try:
        git_runner(
            ["-C", repo_path, "rev-parse", "--verify", "--quiet", f"origin/{base}"]
        )
    except GitError:
        return base
    return f"origin/{base}"
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, with 30 tests in `tests/steps/test_worktree.py` and every pre-existing test still green.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/worktree.py tests/steps/test_worktree.py
git commit -m "feat(steps): resolve origin/<base> with a local fallback"
```

---

## Done when

- `uv run pytest` is green from the worktree root.
- `src/agent_manager/steps/worktree.py` exports `ensure`, `run_git`, `GitError`, `GitRunner`, `worktree_paths`, `branch_exists` and nothing else public.
- No file outside `src/agent_manager/steps/worktree.py` and `tests/steps/test_worktree.py` was changed.
