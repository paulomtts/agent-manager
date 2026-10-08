"""Run a card's verification commands in its worktree and report what happened.

A deterministic step (design §4 `steps/`, §6 "The engine calls `run(ctx) -> dict`.
No network, no model."): it launches the commands the card names and reads their
output. Ported from `ship()` and `verifyError()` in the leave-me-alone plugin's
`scripts/ship.mjs` (:70-90, :62-67), plus the `plainText`/`lastLine` flattening
helpers from `scripts/gh.mjs` (:40-48, :70-73).

Read-only (design §9: "`verify.run_suite` is read-only"). It never commits,
pushes, tags or opens a PR -- `ship.mjs`'s name is historical and that half does
not port -- and it does NOT check for a dirty worktree: design §5 gives that
rule to `review_gate`, and one rule lives in one place.

Every invocation is an argument list handed to `subprocess` (design §5 line
252). `ship.mjs` passed `shell: true`; this port deliberately drops it, so
there is no shell string and nothing to quote.
"""

import os
import re
import shlex
import signal
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ELLIPSIS = "…"
"""One character, appended to text `plain_text` had to cut."""

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def last_line(text: object) -> str:
    """The last non-empty, trimmed line of `text`, or `""`.

    Ported from `gh.mjs`'s `lastLine`: tool managers print activation banners
    above real output, so the value asked for is the last line, not the first.
    `.strip()` also removes the `\\r` of CRLF output, which would otherwise
    travel into the result.
    """
    raw = "" if text is None else str(text)
    lines = [line.strip() for line in raw.split("\n")]
    hits = [line for line in lines if line]
    return hits[-1] if hits else ""


def plain_text(text: object, max_chars: int = 300) -> str:
    """`text` flattened to printable, length-capped text.

    Ported from `gh.mjs`'s `plainText`: ANSI sequences removed, remaining
    control characters collapsed to a single space, trimmed, then truncated
    with one `ELLIPSIS`. A raw ESC byte surviving into a reported field once
    failed a whole milestone after its PRs were already open, and a tail is a
    human hint rather than a payload -- hence both halves.
    """
    raw = "" if text is None else str(text)
    flat = _CONTROL.sub(" ", _ANSI.sub("", raw)).strip()
    return flat if len(flat) <= max_chars else flat[:max_chars] + ELLIPSIS


NO_OUTPUT = "no output"
"""Last-resort diagnostic, so no reported tail or detail is ever blank."""


def command_diagnostic(stdout: object, stderr: object, fallback: object) -> str:
    """One line saying why a command failed, from whichever stream carries it.

    Ported from `ship.mjs`'s `verifyError`. `gh.mjs`'s `ghError` read only
    stderr, which is right for `gh` and `git` and wrong here: verification
    commands are arbitrary repo scripts, and linters and gate scripts routinely
    print their diagnostic to STDOUT and exit non-zero. So: last non-empty line
    of stderr, else last non-empty line of stdout, else `fallback`.
    """
    return (
        last_line(stderr)
        or last_line(stdout)
        or ("" if fallback is None else str(fallback).strip())
        or NO_OUTPUT
    )


def exit_label(exit_code: int) -> str:
    """A red command's exit code as a human reads it.

    `exit 1` for a positive code. A negative code is `subprocess.run`'s way of
    saying the child was killed by that signal, so it is named:
    `exit -9 (signal SIGKILL)`, else `exit -200 (signal 200)` for a number the
    platform does not know. It never raises.
    """
    if exit_code >= 0:
        return f"exit {exit_code}"
    number = -exit_code
    try:
        name = signal.Signals(number).name
    except ValueError:
        name = str(number)
    return f"exit {exit_code} (signal {name})"


@dataclass(frozen=True)
class CommandResult:
    """One finished command: exit code plus its captured streams.

    A plain dataclass, not a Pydantic model: internal-only state that crosses
    no process boundary (`CLAUDE.md`).
    """

    exit_code: int
    stdout: str
    stderr: str


CommandRunner = Callable[..., CommandResult]
"""Takes an argv and a working directory, returns a `CommandResult`.

Called as `runner(argv, cwd)`, and as `runner(argv, cwd, env=overlay)` when the
walk supplied a run or card id (spec e2efd21d): `overlay` holds only the
present `AM_RUN_ID`/`AM_CARD_ID` keys, never a whole environment, so a runner
that never sees ids may keep a strict two-parameter signature.

Raises `FileNotFoundError` or `PermissionError` if the command cannot be
launched at all; a non-zero exit is a return value, not an exception.
"""


class VerifyError(RuntimeError):
    """A verification command that could not be launched at all.

    Distinct from a red suite on purpose: a missing or unrunnable executable is
    a misconfigured card, and reporting it as `passed: false` would send a
    human hunting for a test failure that never happened.
    """

    def __init__(self, message: str, *, argv: list[str]) -> None:
        self.message = message
        self.argv = list(argv)
        super().__init__(f"{message} (argv={self.argv!r})")


RUN_ID_ENV = "AM_RUN_ID"
CARD_ID_ENV = "AM_CARD_ID"
# The two variables a verification command reads to learn which run and card
# it is verifying (spec e2efd21d). Only `run_suite` called by the walk sets them.


def _child_environment(env: Mapping[str, str] | None) -> dict[str, str]:
    """The parent's environment minus both ids, with `env` applied on top.

    Both ids are removed first even when inherited: an `am` that is itself
    some outer run's verification must never report that outer id as this
    command's.
    """
    child = {
        key: value
        for key, value in os.environ.items()
        if key not in (RUN_ID_ENV, CARD_ID_ENV)
    }
    if env is not None:
        child.update(env)
    return child


def run_command(
    argv: list[str], cwd: str, env: Mapping[str, str] | None = None
) -> CommandResult:
    """The default `CommandRunner`: really run `argv` in `cwd`.

    `shell=False` (the default) is the whole point -- see the module docstring.
    `errors="replace"` keeps a command that emits non-UTF-8 bytes from crashing
    the step; its diagnostic still has to reach a human. `env` is only an
    overlay: the child inherits everything else, never an inherited
    `AM_RUN_ID`/`AM_CARD_ID` (`_child_environment`).
    """
    completed = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        errors="replace",
        env=_child_environment(env),
    )
    return CommandResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


DETAIL_MAX = 600
"""Cap for `detail`, which names the command as well as the diagnostic."""


def _display(command: object, argv: list[str]) -> str:
    """The command as a human reads it: the original string, else its argv."""
    return command if isinstance(command, str) else " ".join(argv)


# The two files `run_suite` writes in `log_dir`; `cli.logs` reads them back.
STDOUT_LOG = "stdout.log"
STDERR_LOG = "stderr.log"


def _start_logs(log_dir: Path) -> None:
    """Create (or truncate) both logs, so a suite that dies early still leaves them."""
    for name in (STDOUT_LOG, STDERR_LOG):
        (log_dir / name).write_text("", encoding="utf-8")


def _append_section(path: Path, header: str, text: str) -> None:
    """One command's section: its header line, then `text` verbatim.

    A trailing newline is added only when `text` lacks one, so the next header
    always starts its own line. The file is closed -- flushed -- before the
    next command starts, so a process that dies mid-suite keeps what finished.
    `errors="replace"` matches `run_command`'s read side: a stream can never
    make the log write itself raise anything but an `OSError`.
    """
    with path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(header)
        handle.write(text)
        if text and not text.endswith("\n"):
            handle.write("\n")


def _log_command(log_dir: Path, shown: str, completed: CommandResult) -> None:
    """Append one section per stream for a command that ran."""
    header = f"==> {shown} ({exit_label(completed.exit_code)})\n"
    _append_section(log_dir / STDOUT_LOG, header, completed.stdout)
    _append_section(log_dir / STDERR_LOG, header, completed.stderr)


def _argv_for(command: object) -> list[str] | None:
    """`command` as an argv, or `None` if it is a blank entry to skip.

    A string is split with `shlex.split` -- never handed to a shell, so a `>`
    or `;` inside it stays a literal argument. An already-split sequence is
    used as given. Anything else is a malformed card, raised before a single
    process starts so a typo can never read as a passing suite.
    """
    if isinstance(command, str):
        if command.strip() == "":
            return None
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ValueError(
                f"verify.run_suite could not split command {command!r}: {exc}"
            ) from exc
    elif isinstance(command, Sequence) and not isinstance(
        command, (bytes, bytearray)
    ):
        parts = list(command)
        if not parts:
            return None
        if not all(isinstance(part, str) for part in parts):
            raise ValueError(
                f"verify.run_suite needs a command of strings, got {command!r}"
            )
        argv = [str(part) for part in parts]
    else:
        raise ValueError(
            f"verify.run_suite needs a command string or argv, got {command!r}"
        )

    if not argv:
        raise ValueError(
            f"verify.run_suite got a command that splits to nothing: {command!r}"
        )
    return argv


def _plan_commands(commands: object) -> list[tuple[object, list[str]]]:
    """Every runnable command paired with its argv, validated up front."""
    if isinstance(commands, (str, bytes, bytearray)) or not isinstance(
        commands, Iterable
    ):
        raise ValueError(
            f"verify.run_suite needs a sequence of commands, got {commands!r}"
        )
    planned: list[tuple[object, list[str]]] = []
    for command in commands:
        argv = _argv_for(command)
        if argv is not None:
            planned.append((command, argv))
    return planned


def _field(mapping: object, name: str) -> object:
    """Read ``name`` off a mapping, or ``None`` if it is not a mapping at all.

    The same tolerant read as `reducers._field`: `explore` arrives as whatever
    the Explore phase's result was bound to, and a missing or odd-shaped value
    means "Explore named nothing extra", not a crash.
    """
    return mapping.get(name) if isinstance(mapping, Mapping) else None


_NONE_LIKE = re.compile(r"^\s*(?:none|n/a|null|nil)\b", re.IGNORECASE)


def _is_none_like(command: object) -> bool:
    """True when Explore wrote "there is none" instead of an empty string.

    Explore is an LLM and `typecheck`/`lint` are free text, so a repo with no
    typecheck can come back as `none (CLAUDE.md: there is no separate ...)`.
    Running its first word as a program failed the verify phase and escalated a
    whole run. Only Explore's fields get this leniency: a `--verify` command the
    user typed is always run exactly as given.
    """
    return isinstance(command, str) and _NONE_LIKE.match(command) is not None


def _explore_command_runnable(argv: list[str], worktree_path: str | None) -> bool:
    """Whether `argv[0]` resolves to something actually runnable.

    A second, broader defense alongside `_is_none_like`: that regex only
    catches a sentence that *starts* with `none`/`n/a`/`null`/`nil`, but
    Explore has also been seen writing e.g. `"no separate lint command; ..."`
    -- "no", not "none" -- which the regex misses entirely. Rather than grow
    the regex indefinitely, this checks the thing that actually matters:
    whether `argv[0]` resolves against `PATH` or, for a relative path,
    against the worktree. A true typo in a real command still surfaces
    normally; a stray sentence is treated as the blank case `_argv_for`
    already skips.
    """
    import shutil

    head = argv[0]
    if shutil.which(head) is not None:
        return True
    path = Path(head)
    if path.is_absolute():
        return path.is_file()
    if worktree_path is not None:
        return (Path(worktree_path) / head).is_file()
    return path.is_file()


def _plan_explore_commands(
    explore: object, worktree_path: str | None = None
) -> list[tuple[object, list[str]]]:
    """Explore's `verification.typecheck` then each `verification.lint` entry.

    Pygents design G9 item 4: these run after the `--verify` commands, in that
    order. `fullSuite`/`full_suite` is deliberately not read -- `commands` is
    the suite's only source. A blank or none-like (`none`, `n/a`, ...) typecheck or lint entry is skipped;
    a wrong-typed one raises `ValueError` here, before any process starts,
    exactly like a malformed `--verify` command. `typecheck` and `lint` carry
    no alias in `results.Verification`, so the engine's snake_case dump and a
    hand-written camelCase result use the same two keys.

    An entry whose `argv[0]` does not resolve to anything runnable
    (`_explore_command_runnable`) is also skipped, exactly like a blank
    entry: see that function's docstring for why this catches cases
    `_is_none_like` doesn't.
    """
    verification = _field(explore, "verification")
    planned: list[tuple[object, list[str]]] = []

    typecheck = _field(verification, "typecheck")
    if typecheck is not None and not _is_none_like(typecheck):
        argv = _argv_for(typecheck)
        if argv is not None and _explore_command_runnable(argv, worktree_path):
            planned.append((typecheck, argv))

    lint = _field(verification, "lint")
    if lint is not None:
        if isinstance(lint, (list, tuple)):
            lint = [entry for entry in lint if not _is_none_like(entry)]
        for entry, argv in _plan_commands(lint):
            if _explore_command_runnable(argv, worktree_path):
                planned.append((entry, argv))

    return planned


def _required_worktree(worktree: object) -> str:
    """An existing absolute directory as a string, or `ValueError` up front.

    The same pre-flight shape `worktree.ensure` uses: a bad path must fail
    loudly here, not as a confusing failure from every command in the suite.
    """
    if not isinstance(worktree, (str, Path)):
        raise ValueError(
            f"verify.run_suite needs an absolute worktree path, got {worktree!r}"
        )
    text = str(worktree).strip()
    if text == "" or not Path(text).is_absolute() or not Path(text).is_dir():
        raise ValueError(
            f"verify.run_suite needs an existing absolute worktree directory, "
            f"got {worktree!r}"
        )
    return text


def _id_overlay(run_id: object, card: object) -> dict[str, str]:
    """The `AM_RUN_ID`/`AM_CARD_ID` overlay for the ids that are present.

    `None` and a blank string both mean absent and are left out. Anything
    else that is not a `str` is a caller bug, raised up front like
    `run_suite`'s other `ValueError`s, before a single process starts.
    """
    overlay: dict[str, str] = {}
    for name, key, value in (
        ("run_id", RUN_ID_ENV, run_id),
        ("card", CARD_ID_ENV, card),
    ):
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(
                f"verify.run_suite needs {name} to be a string, got {value!r}"
            )
        if value.strip() == "":
            continue
        overlay[key] = value
    return overlay


def run_suite(
    commands: object,
    worktree: object,
    explore: object = None,
    *,
    runner: CommandRunner = run_command,
    log_dir: Path | None = None,
    run_id: str | None = None,
    card: str | None = None,
) -> dict[str, object]:
    """Run each verification command in `worktree` and report what happened.

    The deterministic phase's result (design §6) -- a plain dict, since it
    crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`).
    `runner` defaults to real execution and exists to be swapped in tests, the
    same callable-injection seam `worktree.py` uses for git.

    `explore` is the Explore phase's result, bound by parameter name by
    `walk.bind_arguments` (no workflow edit needed; the integrate workflow
    has no such phase and gets `None`). Its `verification.typecheck` and each
    `verification.lint` command run after `commands` and are reported, and fail
    the suite, exactly like `--verify` commands (pygents design G9 item 4).
    Every command, extra or not, is planned before the first one runs.

    `log_dir` is the attempt directory `walk.run_one_step` hands a step that
    declares it (spec e1b1e7d5). When set, `stdout.log` and `stderr.log` are
    created there before the first command, and every command that ran
    appends a `==> <command> (<exit label>)` section with its full stream,
    verbatim. It lives under the data directory, never the worktree, so this
    step stays read-only with respect to the repository. An `OSError` writing
    it propagates. The returned dict is the same with or without it.

    `run_id` (injected by `walk.run_one_step`) and `card` (bound from the
    table) become `AM_RUN_ID`/`AM_CARD_ID` in every command's environment,
    explore extras included (spec e2efd21d). With neither present -- a direct
    call -- the runner is called as `runner(argv, cwd)`, exactly as before,
    and `run_command` then runs the command with neither variable set.
    """
    worktree_path = _required_worktree(worktree)
    planned = [
        *_plan_commands(commands),
        *_plan_explore_commands(explore, worktree_path),
    ]
    overlay = _id_overlay(run_id, card)
    if log_dir is not None:
        _start_logs(Path(log_dir))
    result: dict[str, object] = {"passed": False, "verified": [], "detail": ""}
    verified: list[dict[str, object]] = result["verified"]  # type: ignore[assignment]

    for command, argv in planned:
        try:
            if overlay:
                completed = runner(argv, worktree_path, env=overlay)
            else:
                completed = runner(argv, worktree_path)
        except (FileNotFoundError, PermissionError, NotADirectoryError) as exc:
            raise VerifyError(
                f"could not run {_display(command, argv)}: {exc}", argv=argv
            ) from exc

        shown = _display(command, argv)
        if log_dir is not None:
            _log_command(Path(log_dir), shown, completed)

        if completed.exit_code == 0:
            verified.append(
                {
                    "command": command,
                    "ok": True,
                    "tail": plain_text(last_line(completed.stdout)),
                }
            )
            continue

        # The label leads, so no stream content -- a green-looking summary, or
        # a line long enough to be truncated -- can hide that the command
        # failed. No fallback: the label already carries the code.
        label = exit_label(completed.exit_code)
        diagnostic = command_diagnostic(completed.stdout, completed.stderr, None)
        verified.append(
            {
                "command": command,
                "ok": False,
                "exit_code": completed.exit_code,
                "tail": plain_text(f"{label} — {diagnostic}"),
            }
        )
        result["detail"] = plain_text(
            f"verification failed: {shown} — {label} — {diagnostic}", DETAIL_MAX
        )
        # Nothing is marked done after a red command (ship.mjs:89).
        return result

    result["passed"] = True
    return result
