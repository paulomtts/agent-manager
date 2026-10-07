"""The Integrate resolver document, driven end to end (Integrate addendum I3,
card b4bd3795).

Engine tier per design §14. Everything is real except the harness:
`workflow.integrate.INTEGRATE`, `runtime.engine.run_subtask`,
`dispatch.AgentRunner` as the injected agent runner (it owns the attempt
directories, the gates, the retry and the feedback, so a bare lambda would
prove none of them), the store and journal, and temporary git repositories
with a conflict left in progress by `steps.integrate.merge_tip`. Only the
adapter and the launcher are doubles. The launcher plays the resolver and, per
rule 1, learns the conflicting files only by parsing the brief it is handed.
It never asks git for that list, never computes a plan hash and never commits
documents.

Every scenario asserts that the base branch, both story branches and the bare
`origin` are unchanged (rule 4): Integrate never writes the base and never
pushes.
"""

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agent_manager import dispatch, models, prompt
from agent_manager.store import writer as store_writer
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.walk import SubtaskSummary
from agent_manager.harness.base import Outcome
from agent_manager.steps.integrate import merge_tip
from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow import integrate as integrate_workflow

pytestmark = pytest.mark.git

RUN_ID = "run-2026-09-25-integrate"
STORY_ID = "integrate"
"""The synthetic story "Integrate" (addendum I3) every resolver attempt hangs from."""
TIP_CARD = "b0b0b0b0"
"""The synthetic subtask's card: the conflicting story's id (addendum I3)."""
BASE = "main"
INTEGRATION_BRANCH = "m5-integrate"
STORY_A = "m5/story-a"
STORY_B = "m5/story-b"
CONFLICTED = ["a.txt", "b.txt"]
BOTH_SIDES = "story a\nstory b\n"
PWD_SUITE = [[sys.executable, "-c", "import os; print(os.getcwd())"]]
"""A verification command whose output proves which directory it ran in."""
RED_SUITE = [[sys.executable, "-c", "import sys; sys.exit(3)"]]


# ── git helpers ──────────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in `cwd`, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _merge_head(worktree: Path) -> str | None:
    """MERGE_HEAD's sha while a merge is in progress in `worktree`, else None."""
    completed = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _story_branch(repo: Path, scratch_root: Path, branch: str, body: str) -> None:
    """Cut `branch` from the base in a throwaway worktree and overwrite the
    same line of every file in `CONFLICTED`, so two such branches conflict."""
    scratch = scratch_root / f"scratch-{branch.replace('/', '-')}"
    _git(repo, "worktree", "add", str(scratch), "-b", branch, BASE)
    for name in CONFLICTED:
        (scratch / name).write_text(body, encoding="utf-8")
    _git(scratch, "add", *CONFLICTED)
    _git(scratch, "commit", "-m", f"work on {branch}")
    _git(repo, "worktree", "remove", str(scratch))


def _protected_state(repo: Path) -> dict[str, str]:
    """Everything Integrate must never change: the base, both story tips, the
    main checkout and every ref on the bare `origin`."""
    return {
        "base": _git(repo, "rev-parse", f"refs/heads/{BASE}").strip(),
        STORY_A: _git(repo, "rev-parse", f"refs/heads/{STORY_A}").strip(),
        STORY_B: _git(repo, "rev-parse", f"refs/heads/{STORY_B}").strip(),
        "checked_out": _git(repo, "symbolic-ref", "HEAD").strip(),
        "status": _git(repo, "status", "--porcelain"),
        "origin_refs": _git(repo, "ls-remote", "origin"),
    }


# ── the scene: a real conflict left in progress ──────────────────────────────


@dataclass
class Scene:
    repo: Path
    worktree: Path
    conflict_files: list[str]
    protected: dict[str, str]


@pytest.fixture
def scene(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Scene:
    """A repo on `main` with a bare `origin`, two story branches that edit the
    same line of `a.txt` and `b.txt`, story A merged into the integration
    branch, and story B's merge left in progress by `merge_tip`."""
    # Store, journal and attempt directories all live under data_dir().
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", BASE, str(repo)], capture_output=True, text=True, check=True
    )
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "agent-manager tests")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    for name in CONFLICTED:
        (repo / name).write_text("shared line\n", encoding="utf-8")
    _git(repo, "add", "README.md", *CONFLICTED)
    _git(repo, "commit", "-m", "base")

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(repo), str(origin)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "fetch", "origin")

    _story_branch(repo, tmp_path, STORY_A, "story a\n")
    _story_branch(repo, tmp_path, STORY_B, "story b\n")
    protected = _protected_state(repo)

    worktree = tmp_path / "integrate-wt"
    first = merge_tip(repo, worktree, INTEGRATION_BRANCH, BASE, STORY_A)
    assert first["conflict"] is False
    assert first["merged"] == STORY_A
    second = merge_tip(repo, worktree, INTEGRATION_BRANCH, BASE, STORY_B)
    assert second["conflict"] is True
    assert second["files"] == CONFLICTED
    assert _merge_head(worktree) is not None

    return Scene(
        repo=repo,
        worktree=worktree,
        conflict_files=list(second["files"]),
        protected=protected,
    )


def _assert_nothing_protected_moved(scene: Scene) -> None:
    after = _protected_state(scene.repo)
    assert after == scene.protected
    assert INTEGRATION_BRANCH not in after["origin_refs"]


# ── the harness doubles ──────────────────────────────────────────────────────


class _FakeAdapter:
    """A `HarnessAdapter` by shape: its argv names the brief and the result path."""

    name = "fake"
    capabilities = frozenset({"bash", "edit"})

    def build_command(self, d: models.Dispatch) -> list[str]:
        return [
            "fake-resolver",
            "--prompt",
            str(d.prompt_path),
            "--result",
            str(d.result_path),
        ]


_CONFLICT_HEADING = "\n## conflict_files\n"


def _conflict_files_from(brief: str) -> list[str]:
    """Rule 1: the conflict list, parsed out of the brief text and nowhere else."""
    start = brief.index(_CONFLICT_HEADING) + len(_CONFLICT_HEADING)
    files, _end = json.JSONDecoder().raw_decode(brief, start)
    return files


Act = Callable[[int, Path, list[str]], dict[str, Any] | str]
"""What the fake resolver does on attempt `n` in `worktree` for `files`; returns
the result it writes (a dict is dumped as JSON, a str is written raw)."""


@dataclass
class _FakeResolver:
    """A `LauncherFn` double that plays the resolver in the worktree it is run in."""

    act: Act
    prompts: list[str] = field(default_factory=list)
    seen_files: list[list[str]] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        brief = Path(argv[argv.index("--prompt") + 1]).read_text(encoding="utf-8")
        self.prompts.append(brief)
        files = _conflict_files_from(brief)
        self.seen_files.append(files)
        result = self.act(len(self.prompts), Path(cwd), files)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("", encoding="utf-8")
        text = result if isinstance(result, str) else json.dumps(result)
        Path(argv[argv.index("--result") + 1]).write_text(text, encoding="utf-8")
        return Outcome(
            argv=list(argv),
            exit_code=0,
            timed_out=False,
            duration=0.5,
            stdout_path=stdout_path,
        )


def _resolve_properly(attempt: int, worktree: Path, files: list[str]) -> dict[str, Any]:
    for name in files:
        (worktree / name).write_text(BOTH_SIDES, encoding="utf-8")
    _git(worktree, "add", *files)
    _git(worktree, "commit", "--no-edit")
    return {"resolved": True, "summary": f"kept both stories' line in {', '.join(files)}"}


def _commit_with_markers(attempt: int, worktree: Path, files: list[str]) -> dict[str, Any]:
    """Finishes the merge commit on attempt 1 with the markers still in the
    files, then on attempt 2 changes nothing and claims success again."""
    if attempt == 1:
        _git(worktree, "add", *files)
        _git(worktree, "commit", "--no-edit")
    return {"resolved": True, "summary": "committed the merge"}


def _claim_without_touching(attempt: int, worktree: Path, files: list[str]) -> dict[str, Any]:
    return {"resolved": True, "summary": "all conflicts resolved"}


def _write_an_invalid_result(attempt: int, worktree: Path, files: list[str]) -> str:
    # `ResolveResult` is strict: a string is not a bool.
    return json.dumps({"resolved": "yes", "summary": "done"})


# ── driving the document ─────────────────────────────────────────────────────


@dataclass
class _Ran:
    summary: SubtaskSummary
    attempts: list[tuple[str, str]]


def _run(
    scene: Scene,
    resolver: _FakeResolver,
    *,
    commands: list[Any] = PWD_SUITE,
    extra_context: dict[str, Any] | None = None,
) -> _Ran:
    """`runtime.engine.run_subtask(INTEGRATE, ...)` for one synthetic subtask,
    with the real `dispatch.AgentRunner` as the agent runner."""
    workflow = integrate_workflow.INTEGRATE
    subtask = models.SubtaskRun(
        card_id=TIP_CARD,
        branch=INTEGRATION_BRANCH,
        base_branch=BASE,
        status="started",
        worktree_path=scene.worktree,
    )
    context = (
        {"merge_tip": STORY_B, "conflict_files": list(scene.conflict_files)}
        if extra_context is None
        else extra_context
    )
    adapter = _FakeAdapter()
    store = store_writer.Store.open(scene.repo, RUN_ID)
    try:
        store.record_story(
            models.StoryRun(card_id=STORY_ID, title="Integrate", level=0, status="started")
        )
        store.record_subtask(STORY_ID, subtask)
        runner = dispatch.AgentRunner(
            store=store,
            launcher=resolver,
            run_id=RUN_ID,
            story_id=STORY_ID,
            card_id=TIP_CARD,
            adapters={adapter.name: adapter},
            harness_map={
                "resolver": models.HarnessAssignment(harness=adapter.name, model="fake-model")
            },
        )
        summary = runtime_engine.run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=subtask,
            repo_dir=scene.repo,
            commands=commands,
            extra_context=context,
            agent_runner=runner,
        )
        attempts = [
            tuple(row)
            for row in store.connection.execute(
                "SELECT phase, status FROM attempts ORDER BY phase, n"
            ).fetchall()
        ]
    finally:
        store.close()
    return _Ran(summary=summary, attempts=attempts)


# ── the three scenarios the spec names ───────────────────────────────────────


def test_a_resolver_that_finishes_the_merge_ends_done_and_verify_runs_in_the_worktree(
    scene: Scene,
) -> None:
    resolver = _FakeResolver(_resolve_properly)

    ran = _run(scene, resolver)

    assert ran.summary.status == "done", ran.summary.detail
    assert ran.summary.failed_phase is None
    assert ran.attempts == [("resolve", "ok")]
    assert resolver.seen_files == [CONFLICTED]
    brief = resolver.prompts[0]
    assert f"\n## merge_tip\n{STORY_B}\n" in brief
    assert f"\n## branch\n{INTEGRATION_BRANCH}\n" in brief
    assert f"\n## base_branch\n{BASE}\n" in brief
    assert prompt.FEEDBACK_HEADING not in brief
    assert ran.summary.results["resolve"]["resolved"] is True

    verified = ran.summary.results["verify"]
    assert verified["passed"] is True
    assert Path(verified["verified"][0]["tail"]).resolve() == scene.worktree.resolve()

    assert _merge_head(scene.worktree) is None
    parents = _git(scene.worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
    assert len(parents) == 2  # a real merge commit
    for name in CONFLICTED:
        assert (scene.worktree / name).read_text(encoding="utf-8") == BOTH_SIDES
    _assert_nothing_protected_moved(scene)


def test_leftover_markers_are_retried_once_with_the_gates_feedback_then_escalate(
    scene: Scene,
) -> None:
    resolver = _FakeResolver(_commit_with_markers)

    ran = _run(scene, resolver)

    assert ran.summary.status == "escalated"
    assert ran.summary.failed_phase == "resolve"
    assert ran.attempts == [("resolve", "gate_failed"), ("resolve", "gate_failed")]
    assert "Conflict markers remain in: a.txt, b.txt" in ran.summary.detail

    first, second = resolver.prompts
    assert prompt.FEEDBACK_HEADING not in first
    assert prompt.FEEDBACK_HEADING in second
    feedback = second.split(prompt.FEEDBACK_HEADING, 1)[1]
    assert "merge_completed_gate" in feedback
    assert "Conflict markers remain in: a.txt, b.txt" in feedback
    assert resolver.seen_files == [CONFLICTED, CONFLICTED]

    assert "verify" not in ran.summary.results
    _assert_nothing_protected_moved(scene)


def test_a_resolved_flag_without_a_finished_merge_is_rejected_by_git(
    scene: Scene,
) -> None:
    """Rule 3: the flag is advisory. The resolver says `resolved: true` twice
    and git, which still has MERGE_HEAD, says no both times."""
    resolver = _FakeResolver(_claim_without_touching)

    ran = _run(scene, resolver)

    assert ran.summary.status == "escalated"
    assert ran.summary.failed_phase == "resolve"
    assert ran.attempts == [("resolve", "gate_failed"), ("resolve", "gate_failed")]
    assert "MERGE_HEAD exists" in ran.summary.detail
    assert _merge_head(scene.worktree) is not None  # left for a human, never aborted
    assert "verify" not in ran.summary.results
    _assert_nothing_protected_moved(scene)


# ── review focus ─────────────────────────────────────────────────────────────


def test_a_result_that_fails_the_schema_twice_escalates_at_resolve(scene: Scene) -> None:
    resolver = _FakeResolver(_write_an_invalid_result)

    ran = _run(scene, resolver)

    assert ran.summary.status == "escalated"
    assert ran.summary.failed_phase == "resolve"
    assert ran.attempts == [("resolve", "schema_invalid"), ("resolve", "schema_invalid")]
    assert prompt.FEEDBACK_HEADING in resolver.prompts[1]
    assert _merge_head(scene.worktree) is not None
    assert "verify" not in ran.summary.results
    _assert_nothing_protected_moved(scene)


def test_a_clean_resolve_followed_by_a_red_suite_escalates_at_verify(scene: Scene) -> None:
    resolver = _FakeResolver(_resolve_properly)

    ran = _run(scene, resolver, commands=RED_SUITE)

    assert ran.summary.status == "escalated"
    assert ran.summary.failed_phase == "verify"
    assert ran.attempts == [("resolve", "ok")]
    assert "verification failed" in ran.summary.detail
    assert "verification_passed_gate" in ran.summary.detail
    _assert_nothing_protected_moved(scene)


def test_an_extra_context_without_merge_tip_fails_before_any_dispatch(scene: Scene) -> None:
    resolver = _FakeResolver(_resolve_properly)

    with pytest.raises(EngineError) as caught:
        _run(scene, resolver, extra_context={"conflict_files": list(scene.conflict_files)})

    assert caught.value.phase == "resolve"
    assert caught.value.parameter == "merge_tip"
    assert resolver.prompts == []
    assert _merge_head(scene.worktree) is not None
    _assert_nothing_protected_moved(scene)
