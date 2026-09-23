"""How a harness process is actually started (design §4 line 132, decision D7).

D7 says v1 launches harnesses full-auto with cwd pinned to the subtask
worktree, and puts confinement behind a seam: the adapter takes a launcher,
`direct` | `bwrap` | `container`, and only `direct` is implemented. This module
is that seam. The seam, not the confinement, is the deliverable here.

Everything this module does is deliberately blind to what it is running. It
never reads the result file (§6 step 5 is the engine's), never parses the log
it wrote (that is `parse_usage`'s job, on the adapter), and never builds a
shell string (§5 line 252). That blindness is what lets one launcher serve
every adapter.

The line between raising and returning is drawn on retryability. A non-zero
exit and a timeout are `Outcome`s, because §6 wants them journalled as attempts
and a re-dispatch might well succeed. A missing worktree, an empty argv and a
useless timeout are raised, because no retry fixes them and swallowing them as
`harness_error` would burn `max_attempts` re-dispatching into a situation that
cannot change.
"""

import os
import subprocess
import time
from pathlib import Path
from typing import Protocol

from agent_manager.harness.base import Outcome
from agent_manager.models import Launcher


class UnsupportedLauncherError(RuntimeError):
    """A launcher mode that is named but cannot run.

    Carries the requested `kind` and a reason, matching `RoleBundleError`'s
    shape: the caller journals the message, and "unsupported" without the mode
    name is unactionable when three modes exist.
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


def run_direct(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    stdout_path: Path,
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
        )
        try:
            exit_code: int | None = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            process.kill()
            # Reap it, so the wait status is collected and the log file has no
            # writer left when we close it.
            process.wait()
            exit_code = None
            timed_out = True

    return Outcome(
        argv=list(argv),
        exit_code=exit_code,
        timed_out=timed_out,
        duration=time.monotonic() - started,
        stdout_path=stdout_path,
    )


LAUNCHERS: dict[str, LauncherFn | None] = {
    "direct": run_direct,
    "bwrap": None,
    "container": None,
}
"""Every mode `models.Launcher` names, mapped to its implementation or `None`.

The two `None`s are the seam, spelled out. Leaving `bwrap` and `container` out
of this dict entirely would make asking for one an "unknown launcher" -- which
is wrong, they are known, they are simply not built -- and would lose the only
place in the code where D7's deferred work is visible.
"""


def get_launcher(kind: Launcher) -> LauncherFn:
    """Resolve a launcher mode to the function the engine will inject.

    Called once, at run start, with `RunConfig.launcher`. Failing here means
    failing before a single worktree is created, which is the whole reason the
    unimplemented modes are named rather than omitted.
    """
    if kind not in LAUNCHERS:
        raise UnsupportedLauncherError(
            kind,
            reason=(
                "not a launcher mode; the modes are direct, bwrap and container"
            ),
        )
    implementation = LAUNCHERS[kind]
    if implementation is None:
        raise UnsupportedLauncherError(
            kind,
            reason=(
                "is a seam, not an implementation -- v1 implements only "
                "direct, which launches with permissions bypassed and cwd "
                "pinned to the subtask worktree (D7)"
            ),
        )
    return implementation
