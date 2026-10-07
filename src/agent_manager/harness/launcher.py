"""How a harness process is actually started (design §4 line 132, decision D7).

D7 launches harnesses full-auto with cwd pinned to the subtask worktree and
puts confinement behind a seam: the adapter takes a launcher, `direct` |
`bwrap` | `unshare` | `container`. `bwrap` and `unshare` run the harness in a
PID namespace of its own (run-hardening design, Story A); `container` is
still only a name. `wrap_argv` is the one place the isolating forms are
spelled, and `probe` is the one place that asks whether this host runs them.

Everything this module does is deliberately blind to what it is running. It
never reads the result file (§6 step 5 is the engine's), never parses the log
it wrote (nothing does: D4), and never builds a shell string (§5 line 252).
That blindness is what lets one launcher serve every adapter.

The line between raising and returning is drawn on retryability. A non-zero
exit and a timeout are `Outcome`s, because §6 wants them journalled as attempts
and a re-dispatch might well succeed. A missing worktree, an empty argv and a
useless timeout are raised, because no retry fixes them and swallowing them as
`harness_error` would burn `max_attempts` re-dispatching into a situation that
cannot change.
"""

import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from agent_manager.errors import IsolationUnavailableError
from agent_manager.harness.base import Outcome
from agent_manager.models import Launcher


class UnsupportedLauncherError(RuntimeError):
    """A launcher mode that is named but cannot run.

    Carries the requested `kind` and a reason, matching `RoleBundleError`'s
    shape: the caller journals the message, and "unsupported" without the mode
    name is unactionable when four modes exist.
    """

    def __init__(self, kind: str, *, reason: str) -> None:
        self.kind = kind
        self.reason = reason
        super().__init__(f"launcher {kind!r}: {reason}")


class LauncherFn(Protocol):
    """The injected launcher type (§14 line 485).

    Nothing in the engine or an adapter imports `run_direct`; a launcher is
    passed in, which is what lets every test above this module run without
    spawning anything. The Protocol exists so the injection point has a name
    with the real signature on it.
    """

    def __call__(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout: float,
        stdout_path: Path,
    ) -> Outcome: ...


def kill_tree(process: subprocess.Popen[bytes]) -> None:
    """SIGKILL the timed-out process and everything it started.

    A harness spawns workers of its own, and killing only the process we
    spawned leaves them running against the worktree the next attempt reuses,
    still writing into the log we are about to close. `run_direct` puts the
    child in its own session, so the whole tree is one process group.

    The group can already be gone -- the child may exit between the timeout
    firing and the signal -- which is not an error. `process.wait()` still
    runs, so the wait status is collected and nothing is left a zombie.

    A process sharing the manager's own group is killed on its own: signalling
    that group would SIGKILL the manager and every other in-flight worktree
    with it. `run_direct` never produces one, and this is what keeps that a
    fact about the timeout path rather than an assumption.

    The bridge (`runtime/bridge.py`) calls this too, on every process a
    cancelled agent turn spawned: a `to_thread` worker cannot be cancelled, so
    the process it started has to be.
    """
    try:
        group = os.getpgid(process.pid)
    except (ProcessLookupError, PermissionError):
        group = None
    if group is not None and group != os.getpgid(0):
        os.killpg(group, signal.SIGKILL)
    else:
        process.kill()
    process.wait()


_kill_tree = kill_tree
"""The name this was private under; kept so existing callers are unaffected."""


def run_direct(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    stdout_path: Path,
    on_spawn: Callable[[subprocess.Popen[bytes]], None] | None = None,
) -> Outcome:
    """Run `argv` in `cwd`, log to `stdout_path`, kill it after `timeout`.

    `stdout_path` is supplied, not derived: the engine passes
    `attempt_dir(run_id, card, phase, attempt) / "stdout.log"`, which
    `paths.py` roots under `data_dir()` and therefore outside every worktree
    (§6 line 265). Deriving a path here would put a second opinion about run
    layout in a module that has no business holding one. The parent directory
    is created because the caller may hand over a path whose directory does not
    exist yet.

    stdin is devnull: a harness that stops to ask a question reads EOF and
    exits, instead of hanging until the timeout on every single attempt.

    The log is opened for writing, not appending: a resumed run may reuse an
    attempt directory, and a log holding two attempts concatenated is worse
    evidence than a log holding the current one.

    `on_spawn`, when given, is called with the live `Popen` right after it is
    created and before anything waits on it -- the bridge's way of learning
    which process a cancelled turn must kill. If it raises, the child is killed
    and the error propagates: nobody else holds its handle.
    """
    if not argv:
        raise ValueError("launcher argv is empty: there is no program to run")
    if not cwd.is_dir():
        raise NotADirectoryError(
            f"launcher cwd is not a directory: {cwd} -- the worktree step runs "
            f"before any dispatch, so this is a bug above the launcher, not a "
            f"harness failure"
        )
    if not timeout > 0:  # also rejects nan, for which every comparison is false
        raise ValueError(f"launcher timeout must be positive, got {timeout!r}")

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with stdout_path.open("wb") as log, open(os.devnull, "rb") as devnull:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=devnull,
            stdout=log,
            stderr=subprocess.STDOUT,
            # Its own session, so the timeout can kill the whole tree below it
            # and not just the process we spawned.
            start_new_session=True,
        )
        if on_spawn is not None:
            try:
                on_spawn(process)
            except BaseException:
                kill_tree(process)
                raise
        try:
            exit_code: int | None = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            kill_tree(process)
            exit_code = None
            timed_out = True

    return Outcome(
        argv=list(argv),
        exit_code=exit_code,
        timed_out=timed_out,
        duration=time.monotonic() - started,
        stdout_path=stdout_path,
    )


BWRAP_PREFIX: tuple[str, ...] = (
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
)
"""The `bwrap` form (run-hardening design, Story A Design 3).

The whole filesystem is bound read-write, so the harness sees the worktree,
`~/.claude` and every tool exactly as `direct` would; only the PID namespace
is new. `--die-with-parent` takes the namespace down with the process
`run_direct` spawned, so `kill_tree`'s `killpg` still ends everything.
"""

UNSHARE_PREFIX: tuple[str, ...] = (
    "unshare",
    "--user",
    "--map-root-user",
    "--pid",
    "--fork",
    "--mount-proc",
)
"""The fallback form for hosts without `bwrap`.

Plain `unshare --pid --fork` fails `EPERM` for an unprivileged user; a user
namespace mapping the caller to root is what lets it create the PID namespace.
"""

_PREFIXES: dict[str, tuple[str, ...]] = {
    "direct": (),
    "bwrap": BWRAP_PREFIX,
    "unshare": UNSHARE_PREFIX,
}

_UNKNOWN_REASON = (
    "not a launcher mode; the modes are direct, bwrap, unshare and container"
)
_SEAM_REASON = (
    "is a seam, not an implementation -- the implemented modes are direct, "
    "bwrap and unshare"
)


def wrap_argv(mode: str, argv: list[str], cwd: Path) -> list[str]:
    """The argv that runs `argv` under launcher `mode`. Pure: spawns nothing.

    Each element passes through unchanged after the mode's prefix -- no
    quoting, no joining, never a shell string. `cwd` is accepted and unused:
    `bwrap --bind / /` and `unshare` both keep the caller's cwd, and
    `run_direct` both validates it and sets it on the spawn.

    The empty-argv check comes first because, once wrapped, the list is never
    empty and `run_direct`'s own guard would no longer see the mistake.
    """
    if not argv:
        raise ValueError("launcher argv is empty: there is no program to run")
    if mode == "container":
        raise UnsupportedLauncherError(mode, reason=_SEAM_REASON)
    if mode not in _PREFIXES:
        raise UnsupportedLauncherError(mode, reason=_UNKNOWN_REASON)
    return [*_PREFIXES[mode], *argv]


def run_bwrap(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    stdout_path: Path,
    on_spawn: Callable[[subprocess.Popen[bytes]], None] | None = None,
) -> Outcome:
    """`run_direct` on `wrap_argv("bwrap", argv, cwd)`.

    The returned `Outcome.argv` is the wrapped argv, so the journal shows how
    the agent was contained. No probe here: a missing `bwrap` is `run_direct`'s
    ordinary `FileNotFoundError`; choosing a mode the host can run is
    `resolve_isolation`'s job, once, at run start.
    """
    return run_direct(
        wrap_argv("bwrap", argv, cwd),
        cwd=cwd,
        timeout=timeout,
        stdout_path=stdout_path,
        on_spawn=on_spawn,
    )


def run_unshare(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    stdout_path: Path,
    on_spawn: Callable[[subprocess.Popen[bytes]], None] | None = None,
) -> Outcome:
    """`run_direct` on `wrap_argv("unshare", argv, cwd)`; see `run_bwrap`."""
    return run_direct(
        wrap_argv("unshare", argv, cwd),
        cwd=cwd,
        timeout=timeout,
        stdout_path=stdout_path,
        on_spawn=on_spawn,
    )


LAUNCHERS: dict[str, LauncherFn | None] = {
    "direct": run_direct,
    "bwrap": run_bwrap,
    "unshare": run_unshare,
    "container": None,
}
"""Every mode `models.Launcher` names, mapped to its implementation or `None`.

The `None` is the seam, spelled out. Leaving `container` out of this dict
entirely would make asking for it an "unknown launcher" -- which is wrong, it
is known, it is simply not built -- and would lose the only place in the code
where that deferred work is visible.
"""


def get_launcher(kind: Launcher) -> LauncherFn:
    """Resolve a launcher mode to the function the engine will inject.

    Never probes: whether this host can run `bwrap` or `unshare` is decided
    once, at run start, by `resolve_isolation`, and the mode it returns is the
    one passed here. `cli.default_runner_factory` calls it with the run's
    recorded `RunConfig.launcher`. Failing here means failing before a single worktree
    is created, which is why `container` is named rather than omitted.
    """
    if kind not in LAUNCHERS:
        raise UnsupportedLauncherError(kind, reason=_UNKNOWN_REASON)
    implementation = LAUNCHERS[kind]
    if implementation is None:
        raise UnsupportedLauncherError(kind, reason=_SEAM_REASON)
    return implementation


ProbeRunner = Callable[[list[str]], int]
"""Runs an argv and returns its exit code; injected so tests spawn nothing."""

PROBE_TIMEOUT = 10.0
"""Seconds a probe gets. `<form> true` takes milliseconds when it works."""

_PROBEABLE = ("bwrap", "unshare")

_probe_cache: dict[str, str | None] = {}
"""`probe`'s per-process memory, failures included: one probe per mode."""


def default_probe_runner(argv: list[str]) -> int:
    """Run `argv` silently and return its exit code.

    `cwd="/"` so the probe does not depend on the engine's own cwd still
    existing; its own session, like every child of this module. `OSError` and
    `TimeoutExpired` propagate: `probe` turns them into reasons.
    """
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd="/",
        start_new_session=True,
        timeout=PROBE_TIMEOUT,
        check=False,
    ).returncode


def probe(mode: str, *, runner: ProbeRunner | None = None) -> str | None:
    """`None` if this host can start `mode`, else a one-line reason.

    Runs `wrap_argv(mode, ["true"], Path("/"))` once per process: the first
    answer for a mode, failure included, is cached and every later call returns
    it without running anything, whichever `runner` it is given. The reason
    starts with the probe argv, so an operator can rerun it by hand. An
    exception other than `OSError` or `TimeoutExpired` is a bug, not
    "unavailable": it propagates and nothing is cached.
    """
    if mode not in _PROBEABLE:
        raise ValueError(f"only bwrap and unshare can be probed, not {mode!r}")
    if mode in _probe_cache:
        return _probe_cache[mode]
    argv = wrap_argv(mode, ["true"], Path("/"))
    shown = " ".join(argv)
    run = default_probe_runner if runner is None else runner
    try:
        exit_code = run(argv)
        reason = None if exit_code == 0 else f"{shown} exited {exit_code}"
    except subprocess.TimeoutExpired:
        reason = f"{shown} timed out after {PROBE_TIMEOUT:g}s"
    except OSError as error:
        reason = f"{shown} could not start: {error}"
    _probe_cache[mode] = reason
    return reason


def clear_probe_cache() -> None:
    """Forget every cached probe result. Tests only; production never clears."""
    _probe_cache.clear()


IsolationRequest = Literal["auto", "bwrap", "unshare", "none"]
"""What an operator can ask for; `resolve_isolation` turns it into a `Launcher`."""

ISOLATION_NONE_WARNING = (
    "isolation: none (bwrap and unshare are unavailable): agents can signal the engine"
)


@dataclass(frozen=True)
class Isolation:
    """The launcher mode a run uses, and the warning to surface if any."""

    mode: Launcher
    warning: str | None


def resolve_isolation(
    requested: str, *, runner: ProbeRunner | None = None
) -> Isolation:
    """Turn an isolation request into the launcher mode this host can run.

    `none` probes nothing. An explicit `bwrap` or `unshare` probes only itself
    and never falls back: an operator who named a mode gets it or an
    `IsolationUnavailableError`. `auto` tries `bwrap`, then `unshare` (still
    isolated, so no warning), then lands on `direct` with
    `ISOLATION_NONE_WARNING`.
    """
    if requested == "none":
        return Isolation("direct", None)
    if requested == "bwrap" or requested == "unshare":
        reason = probe(requested, runner=runner)
        if reason is not None:
            raise IsolationUnavailableError(requested, reason)
        return Isolation(requested, None)
    if requested == "auto":
        if probe("bwrap", runner=runner) is None:
            return Isolation("bwrap", None)
        if probe("unshare", runner=runner) is None:
            return Isolation("unshare", None)
        return Isolation("direct", ISOLATION_NONE_WARNING)
    raise ValueError(
        f"isolation must be auto, bwrap, unshare or none, got {requested!r}"
    )
