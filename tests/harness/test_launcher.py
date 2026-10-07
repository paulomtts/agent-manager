"""Behaviour of the direct launcher (design §6 step 4, decision D7, card
55e503e0).

Unit tier per design §14 line 484. The children below are `sys.executable -c`
one-liners -- deterministic, dependency-free, and emphatically not harnesses.
§14's rule is that no *harness* is executed in unit tests because the launcher
is injected everywhere above this module; running a real harness binary is
reserved for the single opt-in, excluded-by-default end-to-end test in §14
lines 489-490, which this card does not add. The launcher's own contract has no
other place to be tested.
"""

import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_manager import dispatch
from agent_manager.errors import IsolationUnavailableError
from agent_manager.harness import launcher
from agent_manager.harness.base import Outcome


def test_a_successful_command_reports_exit_zero_and_captures_stdout(tmp_path):
    # The stdout.log parent is deliberately absent: the engine passes
    # `attempt_dir(...) / "stdout.log"` and the launcher must be willing to
    # create the directory it was handed.
    log = tmp_path / "implement.1" / "stdout.log"
    outcome = launcher.run_direct(
        [sys.executable, "-c", "print('hello from the child')"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
    )
    assert outcome.exit_code == 0
    assert outcome.timed_out is False
    assert outcome.stdout_path == log
    assert outcome.argv[0] == sys.executable
    assert "hello from the child" in log.read_text()


def test_stderr_is_merged_into_the_same_log(tmp_path):
    # One log per attempt (§6 line 270). A harness's diagnostics are the most
    # useful thing in it when an attempt fails, so they must not go to the
    # manager's own stderr, where nothing journals them.
    log = tmp_path / "stdout.log"
    outcome = launcher.run_direct(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('a warning\\n'); print('a result')",
        ],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
    )
    assert outcome.exit_code == 0
    text = log.read_text()
    assert "a warning" in text
    assert "a result" in text


def test_a_non_zero_exit_is_a_value_not_an_exception(tmp_path):
    # §6 line 278 wants a non-zero exit journalled as harness_error, which
    # means the engine has to receive it as data it can record.
    outcome = launcher.run_direct(
        [sys.executable, "-c", "raise SystemExit(3)"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=tmp_path / "stdout.log",
    )
    assert outcome.exit_code == 3
    assert outcome.timed_out is False


def test_a_timeout_kills_the_child_and_returns_a_value(tmp_path):
    log = tmp_path / "stdout.log"
    outcome = launcher.run_direct(
        [
            sys.executable,
            "-c",
            "import time; print('before the sleep', flush=True); time.sleep(30)",
        ],
        cwd=tmp_path,
        timeout=0.2,
        stdout_path=log,
    )
    assert outcome.timed_out is True
    assert outcome.exit_code is None
    # The partial log is the whole point of leaving it on disk: it is the only
    # evidence of what the harness was doing when the clock ran out.
    assert "before the sleep" in log.read_text()
    # The kill actually happened -- we did not just wait out the 30s sleep.
    assert outcome.duration < 20.0


@pytest.mark.soak
def test_a_timeout_kills_the_processes_the_child_started(tmp_path):
    # A harness is itself a process launcher. Killing only the direct child
    # leaves its workers running against the worktree the run is about to
    # reuse, still burning tokens, and still holding the attempt log open --
    # exactly the wedged state the timeout exists to end.
    log = tmp_path / "stdout.log"
    child = (
        "import subprocess, sys, time; "
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print(g.pid, flush=True); "
        "time.sleep(60)"
    )
    outcome = launcher.run_direct(
        [sys.executable, "-c", child],
        cwd=tmp_path,
        timeout=2.0,
        stdout_path=log,
    )
    assert outcome.timed_out is True
    grandchild = int(log.read_text().split()[0])
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(grandchild, signal.SIGKILL)
    pytest.fail(f"process {grandchild} outlived the launcher's timeout kill")


class _FakeProcess:
    """Stands in for a `Popen` whose pid is in the manager's own group."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.killed = False
        self.waited = False

    def kill(self) -> None:
        self.killed = True

    def wait(self) -> None:
        self.waited = True


def test_the_timeout_kill_never_signals_the_managers_own_process_group():
    # `run_direct` puts every child in a session of its own, so the group kill
    # is safe -- but if that ever stops being true, signalling the group would
    # SIGKILL the manager, every other in-flight worktree with it. The kill
    # falls back to the single process instead.
    fake = _FakeProcess(os.getpid())
    launcher._kill_tree(fake)
    assert fake.killed is True
    assert fake.waited is True


def test_the_child_runs_in_the_cwd_it_was_given(tmp_path):
    # D7: cwd pinned to the subtask worktree is the isolation, so a launcher
    # that silently inherited the manager's cwd would run every harness in the
    # wrong repository.
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    log = tmp_path / "stdout.log"
    launcher.run_direct(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd=worktree,
        timeout=30.0,
        stdout_path=log,
    )
    assert Path(log.read_text().strip()).resolve() == worktree.resolve()


def test_duration_is_positive_and_finite(tmp_path):
    outcome = launcher.run_direct(
        [sys.executable, "-c", "import time; time.sleep(0.05)"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=tmp_path / "stdout.log",
    )
    assert outcome.duration > 0
    assert math.isfinite(outcome.duration)


def test_a_missing_or_non_directory_cwd_is_refused(tmp_path):
    # Refusing beats returning an Outcome: classified as harness_error it would
    # be re-dispatched max_attempts times into a directory that never appears.
    with pytest.raises(NotADirectoryError):
        launcher.run_direct(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path / "never-created",
            timeout=30.0,
            stdout_path=tmp_path / "stdout.log",
        )

    a_file = tmp_path / "not-a-worktree.txt"
    a_file.write_text("")
    with pytest.raises(NotADirectoryError):
        launcher.run_direct(
            [sys.executable, "-c", "pass"],
            cwd=a_file,
            timeout=30.0,
            stdout_path=tmp_path / "stdout.log",
        )


def test_a_useless_timeout_is_refused(tmp_path):
    for bad in (0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            launcher.run_direct(
                [sys.executable, "-c", "pass"],
                cwd=tmp_path,
                timeout=bad,
                stdout_path=tmp_path / "stdout.log",
            )


def test_an_empty_argv_is_refused(tmp_path):
    # Without the guard this surfaces from inside subprocess as an IndexError
    # naming nothing the operator can act on.
    with pytest.raises(ValueError):
        launcher.run_direct(
            [],
            cwd=tmp_path,
            timeout=30.0,
            stdout_path=tmp_path / "stdout.log",
        )


def test_a_missing_executable_propagates_rather_than_looking_like_an_exit(tmp_path):
    # A harness binary that is not installed is a configuration problem. If it
    # came back as an ordinary non-zero Outcome the engine would retry it
    # max_attempts times and journal harness_error with no hint why.
    with pytest.raises(FileNotFoundError):
        launcher.run_direct(
            [str(tmp_path / "no-such-harness"), "-p"],
            cwd=tmp_path,
            timeout=30.0,
            stdout_path=tmp_path / "stdout.log",
        )


def test_a_child_that_reads_stdin_gets_eof_instead_of_hanging(tmp_path):
    # An interactive harness prompting for confirmation must not burn the whole
    # timeout on every attempt. The manager's own fd 0 is loaded with readable
    # data for the duration of the call, because under pytest fd 0 is already
    # /dev/null -- a launcher that simply inherited stdin would otherwise pass
    # this test by accident.
    log = tmp_path / "stdout.log"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"y\n")
    os.close(write_fd)
    saved_stdin = os.dup(0)
    try:
        os.dup2(read_fd, 0)
        os.close(read_fd)
        outcome = launcher.run_direct(
            [
                sys.executable,
                "-c",
                "import sys; print('stdin gave', repr(sys.stdin.read()))",
            ],
            cwd=tmp_path,
            timeout=30.0,
            stdout_path=log,
        )
    finally:
        os.dup2(saved_stdin, 0)
        os.close(saved_stdin)
    assert outcome.exit_code == 0
    assert outcome.timed_out is False
    assert "stdin gave ''" in log.read_text()


def test_an_existing_log_from_a_previous_attempt_is_truncated(tmp_path):
    # A resumed run can reuse an attempt directory. Two attempts concatenated
    # is worse evidence than one.
    log = tmp_path / "stdout.log"
    log.write_text("output from the attempt before the crash\n")
    launcher.run_direct(
        [sys.executable, "-c", "print('this attempt')"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
    )
    text = log.read_text()
    assert "this attempt" in text
    assert "before the crash" not in text


def test_get_launcher_direct_returns_the_direct_launcher(tmp_path):
    fn = launcher.get_launcher("direct")
    assert fn is launcher.run_direct
    # It is usable through the injected type's call shape, keyword-only args
    # and all -- the engine never calls run_direct by name.
    injected: launcher.LauncherFn = fn
    outcome = injected(
        [sys.executable, "-c", "print('injected')"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=tmp_path / "stdout.log",
    )
    assert outcome.exit_code == 0


@pytest.mark.parametrize("kind", ["container"])
def test_the_unimplemented_modes_are_named_and_refuse(kind):
    # D7: it is in the mapping on purpose. A deliberate refusal at config
    # time beats a KeyError surfacing mid-run, and it keeps the name
    # discoverable as the seam it is.
    with pytest.raises(launcher.UnsupportedLauncherError) as excinfo:
        launcher.get_launcher(kind)
    assert excinfo.value.kind == kind
    message = str(excinfo.value)
    assert kind in message
    for implemented in ("direct", "bwrap", "unshare"):
        assert implemented in message


def test_an_unknown_launcher_name_raises_the_same_error_type():
    # The Literal catches this at type-check time; the runtime guard exists
    # because RunConfig.launcher can arrive from a journal line written by an
    # older or newer build.
    with pytest.raises(launcher.UnsupportedLauncherError) as excinfo:
        launcher.get_launcher("docker")
    assert excinfo.value.kind == "docker"
    for known in ("direct", "bwrap", "unshare", "container"):
        assert known in str(excinfo.value)


def test_every_launcher_literal_member_is_accounted_for():
    # If models.Launcher grows a fourth mode, this fails rather than letting it
    # fall through to "unknown launcher" at run time.
    from typing import get_args

    from agent_manager.models import Launcher

    assert set(get_args(Launcher)) == set(launcher.LAUNCHERS)


def test_on_spawn_is_called_once_with_the_live_process(tmp_path):
    # The bridge records the process through this hook so a cancelled turn can
    # kill it; a hook that ran after the wait would record a corpse.
    seen = []

    def hook(process):
        seen.append((process, process.poll()))

    outcome = launcher.run_direct(
        [sys.executable, "-c", "import time; time.sleep(0.1); print('done')"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=tmp_path / "stdout.log",
        on_spawn=hook,
    )
    assert len(seen) == 1
    process, polled = seen[0]
    assert polled is None
    assert process.args[0] == sys.executable
    assert process.returncode == outcome.exit_code == 0


def test_passing_no_on_spawn_behaves_as_before(tmp_path):
    log = tmp_path / "stdout.log"
    outcome = launcher.run_direct(
        [sys.executable, "-c", "print('plain')"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
        on_spawn=None,
    )
    assert outcome.exit_code == 0
    assert outcome.timed_out is False
    assert "plain" in log.read_text()


def test_an_on_spawn_that_raises_kills_the_child_and_propagates(tmp_path):
    # Review Focus 4: the hook failing must not leave a harness running for the
    # whole launcher timeout with nobody holding its handle.
    spawned = []

    def hook(process):
        spawned.append(process)
        raise RuntimeError("hook broke")

    with pytest.raises(RuntimeError, match="hook broke"):
        launcher.run_direct(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            timeout=120.0,
            stdout_path=tmp_path / "stdout.log",
            on_spawn=hook,
        )
    assert len(spawned) == 1
    assert spawned[0].poll() is not None


def test_kill_tree_is_public_and_the_old_name_is_an_alias():
    assert launcher._kill_tree is launcher.kill_tree
    fake = _FakeProcess(os.getpid())
    launcher.kill_tree(fake)
    assert fake.killed is True
    assert fake.waited is True


def _marks(test_function) -> set[str]:
    return {mark.name for mark in getattr(test_function, "pytestmark", [])}


def test_only_the_grandchild_kill_launcher_test_is_soak():
    # Test-tier spec V9: the grandchild-kill probe is the one launcher test that
    # belongs in soak. The other two slow-looking tests were shrunk to fit the
    # unit budget so they keep running on every `uv run pytest`; a soak marker
    # creeping back onto them would deselect them silently. Marks are read off
    # the functions because a mark there deselects them but not this guard.
    assert _marks(test_a_timeout_kills_the_processes_the_child_started) == {"soak"}
    assert _marks(test_a_timeout_kills_the_child_and_returns_a_value) == set()
    assert _marks(test_on_spawn_is_called_once_with_the_live_process) == set()


BWRAP_FORM = [
    "bwrap",
    "--bind",
    "/",
    "/",
    "--dev-bind",
    "/dev",
    "/dev",
    "--proc",
    "/proc",
    "--unshare-pid",
    "--die-with-parent",
    "--new-session",
]
"""Run-hardening design §Story A Design 3, written out by hand on purpose:
comparing against `launcher.BWRAP_PREFIX` would pass whatever it says."""

UNSHARE_FORM = [
    "unshare",
    "--user",
    "--map-root-user",
    "--pid",
    "--fork",
    "--mount-proc",
]


def test_wrap_argv_bwrap_is_the_design_form_then_the_argv(tmp_path):
    wrapped = launcher.wrap_argv("bwrap", ["claude", "-p", "hi"], tmp_path)
    assert wrapped == [*BWRAP_FORM, "claude", "-p", "hi"]


def test_wrap_argv_unshare_is_the_design_form_then_the_argv(tmp_path):
    # --user --map-root-user is what lets an unprivileged user create the PID
    # namespace at all; plain `unshare --pid --fork` fails EPERM.
    wrapped = launcher.wrap_argv("unshare", ["claude", "-p", "hi"], tmp_path)
    assert wrapped == [*UNSHARE_FORM, "claude", "-p", "hi"]


def test_wrap_argv_direct_is_an_equal_new_list(tmp_path):
    argv = ["claude", "-p", "hi"]
    wrapped = launcher.wrap_argv("direct", argv, tmp_path)
    assert wrapped == argv
    assert wrapped is not argv


@pytest.mark.parametrize("mode", ["direct", "bwrap", "unshare"])
def test_wrap_argv_does_not_mutate_its_input(mode, tmp_path):
    argv = ["claude", "-p", "hi"]
    launcher.wrap_argv(mode, argv, tmp_path)
    assert argv == ["claude", "-p", "hi"]


@pytest.mark.parametrize(
    ("mode", "prefix"),
    [("direct", []), ("bwrap", BWRAP_FORM), ("unshare", UNSHARE_FORM)],
)
def test_wrap_argv_passes_awkward_elements_through_one_for_one(mode, prefix, tmp_path):
    # No quoting, no joining: the launcher never builds a shell string, so an
    # element with spaces, quotes or a leading -- must arrive as it left.
    argv = ["claude", "-p", "--weird value", "it's \"quoted\"", ""]
    assert launcher.wrap_argv(mode, argv, tmp_path) == [*prefix, *argv]


@pytest.mark.parametrize("mode", ["direct", "bwrap", "unshare"])
def test_wrap_argv_neither_uses_nor_validates_cwd(mode, tmp_path):
    # bwrap --bind / / and unshare keep the caller's cwd; run_direct sets and
    # validates it. A --chdir here would be a second opinion about the cwd.
    cwd = tmp_path / "never-created"
    wrapped = launcher.wrap_argv(mode, ["claude"], cwd)
    assert str(cwd) not in wrapped
    assert "--chdir" not in wrapped


@pytest.mark.parametrize("mode", ["direct", "bwrap", "unshare"])
def test_wrap_argv_refuses_an_empty_argv_for_every_mode(mode, tmp_path):
    # Once wrapped the list is never empty, so run_direct's own guard would no
    # longer catch this; wrap_argv has to.
    with pytest.raises(ValueError, match="argv is empty"):
        launcher.wrap_argv(mode, [], tmp_path)


def test_wrap_argv_refuses_container_as_the_seam_it_is(tmp_path):
    with pytest.raises(launcher.UnsupportedLauncherError) as excinfo:
        launcher.wrap_argv("container", ["claude"], tmp_path)
    assert excinfo.value.kind == "container"
    message = str(excinfo.value)
    for implemented in ("direct", "bwrap", "unshare"):
        assert implemented in message


def test_wrap_argv_refuses_an_unknown_mode_naming_all_four(tmp_path):
    with pytest.raises(launcher.UnsupportedLauncherError) as excinfo:
        launcher.wrap_argv("docker", ["claude"], tmp_path)
    assert excinfo.value.kind == "docker"
    for known in ("direct", "bwrap", "unshare", "container"):
        assert known in str(excinfo.value)


def _recording_run_direct(calls):
    """A stand-in for `run_direct` that records its call and echoes its argv."""

    def fake(argv, *, cwd, timeout, stdout_path, on_spawn=None):
        calls.append(
            {
                "argv": argv,
                "cwd": cwd,
                "timeout": timeout,
                "stdout_path": stdout_path,
                "on_spawn": on_spawn,
            }
        )
        return Outcome(
            argv=list(argv),
            exit_code=0,
            timed_out=False,
            duration=0.01,
            stdout_path=stdout_path,
        )

    return fake


@pytest.mark.parametrize(
    ("mode", "fn_name"), [("bwrap", "run_bwrap"), ("unshare", "run_unshare")]
)
def test_the_isolating_launchers_hand_run_direct_the_wrapped_argv(
    mode, fn_name, tmp_path, monkeypatch
):
    # Everything run_direct guarantees (devnull stdin, truncated log, own
    # session, kill_tree on timeout) carries over only if the isolating
    # launchers go through it with every argument intact.
    calls = []
    monkeypatch.setattr(launcher, "run_direct", _recording_run_direct(calls))

    def hook(process):
        pass

    log = tmp_path / "stdout.log"
    outcome = getattr(launcher, fn_name)(
        ["claude", "-p", "hi"],
        cwd=tmp_path,
        timeout=12.5,
        stdout_path=log,
        on_spawn=hook,
    )
    assert calls == [
        {
            "argv": launcher.wrap_argv(mode, ["claude", "-p", "hi"], tmp_path),
            "cwd": tmp_path,
            "timeout": 12.5,
            "stdout_path": log,
            "on_spawn": hook,
        }
    ]
    assert outcome.exit_code == 0
    assert outcome.stdout_path == log


@pytest.mark.parametrize(
    ("mode", "fn_name"), [("bwrap", "run_bwrap"), ("unshare", "run_unshare")]
)
def test_the_outcome_argv_is_the_wrapped_argv(mode, fn_name, tmp_path, monkeypatch):
    # The journal records Outcome.argv; it must show how the agent was
    # contained, not just what it was asked to run.
    monkeypatch.setattr(launcher, "run_direct", _recording_run_direct([]))
    argv = ["claude", "-p", "hi"]
    outcome = getattr(launcher, fn_name)(
        argv, cwd=tmp_path, timeout=30.0, stdout_path=tmp_path / "stdout.log"
    )
    assert outcome.argv == launcher.wrap_argv(mode, argv, tmp_path)


@pytest.mark.parametrize("fn_name", ["run_bwrap", "run_unshare"])
def test_the_isolating_launchers_receive_the_bridge_spawn_hook(fn_name):
    # dispatch._spawn_kwargs forwards on_spawn only to a launcher that declares
    # it; without the parameter a cancelled turn could not kill the process.
    fn = getattr(launcher, fn_name)
    assert dispatch._spawn_kwargs(fn) == {"on_spawn": None}


def test_get_launcher_returns_each_implemented_mode():
    assert launcher.get_launcher("direct") is launcher.run_direct
    assert launcher.get_launcher("bwrap") is launcher.run_bwrap
    assert launcher.get_launcher("unshare") is launcher.run_unshare


def test_the_launchers_map_is_exactly_the_four_modes():
    assert launcher.LAUNCHERS == {
        "direct": launcher.run_direct,
        "bwrap": launcher.run_bwrap,
        "unshare": launcher.run_unshare,
        "container": None,
    }


@pytest.mark.parametrize("fn_name", ["run_bwrap", "run_unshare"])
def test_an_empty_argv_never_reaches_run_direct(fn_name, tmp_path, monkeypatch):
    # Review Focus: run_direct's own empty-argv guard cannot see an argv that
    # has already been wrapped, so the isolating launchers must refuse first.
    calls = []
    monkeypatch.setattr(launcher, "run_direct", _recording_run_direct(calls))
    with pytest.raises(ValueError, match="argv is empty"):
        getattr(launcher, fn_name)(
            [], cwd=tmp_path, timeout=30.0, stdout_path=tmp_path / "stdout.log"
        )
    assert calls == []


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    # The probe cache is per process; without this, whichever test probes
    # first decides the answer for every later test in the session.
    launcher.clear_probe_cache()
    yield
    launcher.clear_probe_cache()


class _Runner:
    """An injected `ProbeRunner`: records each argv and answers from a script.

    `answers` maps the probe's first element (`bwrap` or `unshare`) to an exit
    code or to an exception instance to raise.
    """

    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        answer = self.answers[argv[0]]
        if isinstance(answer, BaseException):
            raise answer
        return answer


BWRAP_PROBE = [*BWRAP_FORM, "true"]
UNSHARE_PROBE = [*UNSHARE_FORM, "true"]


@pytest.mark.parametrize(
    ("mode", "probe_argv"), [("bwrap", BWRAP_PROBE), ("unshare", UNSHARE_PROBE)]
)
def test_a_probe_that_exits_zero_means_available(mode, probe_argv):
    runner = _Runner(**{mode: 0})
    assert launcher.probe(mode, runner=runner) is None
    assert runner.calls == [probe_argv]


def test_a_probe_that_exits_non_zero_names_the_argv_and_the_code():
    reason = launcher.probe("bwrap", runner=_Runner(bwrap=1))
    assert reason.startswith(" ".join(BWRAP_PROBE))
    assert reason.endswith("exited 1")


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(2, "No such file or directory", "bwrap"),
        PermissionError(13, "Permission denied", "bwrap"),
    ],
)
def test_a_probe_that_cannot_start_is_a_reason_not_an_exception(error):
    reason = launcher.probe("bwrap", runner=_Runner(bwrap=error))
    assert reason.startswith(" ".join(BWRAP_PROBE))
    assert "could not start" in reason


def test_a_probe_that_times_out_is_a_reason():
    timeout = subprocess.TimeoutExpired(BWRAP_PROBE, 10.0)
    reason = launcher.probe("bwrap", runner=_Runner(bwrap=timeout))
    assert reason.startswith(" ".join(BWRAP_PROBE))
    assert "timed out after 10s" in reason


def test_any_other_runner_error_propagates_and_is_not_cached():
    # A runner raising RuntimeError is a bug in the runner, not a host without
    # bwrap; caching it would hide the bug for the rest of the process.
    with pytest.raises(RuntimeError, match="runner bug"):
        launcher.probe("bwrap", runner=_Runner(bwrap=RuntimeError("runner bug")))
    assert launcher.probe("bwrap", runner=_Runner(bwrap=0)) is None


def test_a_probe_result_is_cached_per_process_including_a_failure():
    failing = _Runner(bwrap=1)
    first = launcher.probe("bwrap", runner=failing)
    assert first is not None
    assert launcher.probe("bwrap", runner=failing) == first
    assert len(failing.calls) == 1

    # A different runner does not reopen the question.
    succeeding = _Runner(bwrap=0)
    assert launcher.probe("bwrap", runner=succeeding) == first
    assert succeeding.calls == []

    launcher.clear_probe_cache()
    assert launcher.probe("bwrap", runner=succeeding) is None
    assert len(succeeding.calls) == 1


@pytest.mark.parametrize("mode", ["direct", "container", "docker"])
def test_only_the_isolating_modes_can_be_probed(mode):
    runner = _Runner()
    with pytest.raises(ValueError, match="only bwrap and unshare"):
        launcher.probe(mode, runner=runner)
    assert runner.calls == []


def test_probe_without_a_runner_uses_the_default_runner(monkeypatch):
    seen = []

    def recorder(argv):
        seen.append(list(argv))
        return 0

    monkeypatch.setattr(launcher, "default_probe_runner", recorder)
    assert launcher.probe("unshare") is None
    assert seen == [UNSHARE_PROBE]


def test_the_default_runner_returns_the_exit_code():
    # A sys.executable child, like the run_direct tests above: no bwrap.
    code = launcher.default_probe_runner([sys.executable, "-c", "raise SystemExit(3)"])
    assert code == 3


def test_the_default_runner_lets_a_missing_binary_raise(tmp_path):
    with pytest.raises(FileNotFoundError):
        launcher.default_probe_runner([str(tmp_path / "no-such-bwrap"), "true"])


def test_auto_takes_bwrap_without_probing_unshare():
    runner = _Runner(bwrap=0, unshare=0)
    assert launcher.resolve_isolation("auto", runner=runner) == launcher.Isolation(
        "bwrap", None
    )
    assert runner.calls == [BWRAP_PROBE]


def test_auto_falls_back_to_unshare_without_a_warning():
    # unshare still isolates the engine, so there is nothing to warn about.
    runner = _Runner(bwrap=1, unshare=0)
    assert launcher.resolve_isolation("auto", runner=runner) == launcher.Isolation(
        "unshare", None
    )
    assert runner.calls == [BWRAP_PROBE, UNSHARE_PROBE]


def test_auto_lands_on_direct_with_the_design_warning():
    runner = _Runner(bwrap=1, unshare=FileNotFoundError(2, "No such file", "unshare"))
    isolation = launcher.resolve_isolation("auto", runner=runner)
    assert isolation.mode == "direct"
    assert isolation.warning == (
        "isolation: none (bwrap and unshare are unavailable): "
        "agents can signal the engine"
    )


def test_none_is_direct_and_probes_nothing():
    runner = _Runner()
    assert launcher.resolve_isolation("none", runner=runner) == launcher.Isolation(
        "direct", None
    )
    assert runner.calls == []


@pytest.mark.parametrize(
    ("mode", "probe_argv", "other_argv"),
    [("bwrap", BWRAP_PROBE, UNSHARE_PROBE), ("unshare", UNSHARE_PROBE, BWRAP_PROBE)],
)
def test_an_explicit_mode_that_cannot_start_raises_and_never_falls_back(
    mode, probe_argv, other_argv
):
    runner = _Runner(bwrap=1, unshare=1)
    with pytest.raises(IsolationUnavailableError) as excinfo:
        launcher.resolve_isolation(mode, runner=runner)
    assert excinfo.value.mode == mode
    assert " ".join(probe_argv) in str(excinfo.value)
    assert other_argv not in runner.calls


def test_an_explicit_mode_that_starts_is_used_and_probes_only_itself():
    runner = _Runner(bwrap=0, unshare=0)
    assert launcher.resolve_isolation("unshare", runner=runner) == launcher.Isolation(
        "unshare", None
    )
    assert runner.calls == [UNSHARE_PROBE]


@pytest.mark.parametrize("requested", ["direct", "container", "", "AUTO"])
def test_an_unknown_isolation_request_is_refused(requested):
    runner = _Runner()
    with pytest.raises(ValueError) as excinfo:
        launcher.resolve_isolation(requested, runner=runner)
    for accepted in ("auto", "bwrap", "unshare", "none"):
        assert accepted in str(excinfo.value)
    assert runner.calls == []


def test_resolving_twice_probes_each_mode_once_in_total():
    runner = _Runner(bwrap=1, unshare=1)
    launcher.resolve_isolation("auto", runner=runner)
    launcher.resolve_isolation("auto", runner=runner)
    assert runner.calls == [BWRAP_PROBE, UNSHARE_PROBE]


def test_a_probe_killed_by_a_signal_is_unavailable():
    # Review Focus: Popen reports death by signal as a negative code; that is
    # a failed probe, not a pass.
    reason = launcher.probe("unshare", runner=_Runner(unshare=-9))
    assert reason == " ".join(UNSHARE_PROBE) + " exited -9"


def test_a_runner_that_mutates_its_argv_does_not_change_the_reason():
    # Review Focus: the reason names the argv that was run, whatever the
    # runner did to its copy afterwards.
    def mutating(argv):
        argv.append("--extra")
        return 1

    reason = launcher.probe("bwrap", runner=mutating)
    assert reason == " ".join(BWRAP_PROBE) + " exited 1"


def test_the_default_runner_runs_in_root_with_stdin_at_eof():
    # Review Focus: the engine's cwd may be a worktree that is already gone,
    # and a probe that waited on stdin would hang run start.
    child = (
        "import os, sys; "
        "sys.exit(0 if os.getcwd() == '/' and sys.stdin.read() == '' else 4)"
    )
    assert launcher.default_probe_runner([sys.executable, "-c", child]) == 0


def test_the_default_runner_gives_up_on_a_hung_probe(monkeypatch):
    # Review Focus: a probe that never exits must not stall run start; the
    # TimeoutExpired reaches probe(), which reports it.
    monkeypatch.setattr(launcher, "PROBE_TIMEOUT", 0.2)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        launcher.default_probe_runner(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
    assert time.monotonic() - started < 10.0


def test_the_default_runner_puts_the_probe_in_a_session_of_its_own():
    # Like every child of this module: a probe must not share the engine's
    # session, so nothing it does can reach the engine's process group.
    child = "import os, sys; sys.exit(0 if os.getsid(0) == os.getpid() else 5)"
    assert launcher.default_probe_runner([sys.executable, "-c", child]) == 0
