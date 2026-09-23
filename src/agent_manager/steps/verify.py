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

import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

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


@dataclass(frozen=True)
class CommandResult:
    """One finished command: exit code plus its captured streams.

    A plain dataclass, not a Pydantic model: internal-only state that crosses
    no process boundary (`CLAUDE.md`).
    """

    exit_code: int
    stdout: str
    stderr: str


CommandRunner = Callable[[list[str], str], CommandResult]
"""Takes an argv and a working directory, returns a `CommandResult`.

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


def run_command(argv: list[str], cwd: str) -> CommandResult:
    """The default `CommandRunner`: really run `argv` in `cwd`.

    `shell=False` (the default) is the whole point -- see the module docstring.
    `errors="replace"` keeps a command that emits non-UTF-8 bytes from crashing
    the step; its diagnostic still has to reach a human.
    """
    completed = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        errors="replace",
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


def run_suite(
    commands: object,
    worktree: object,
    *,
    runner: CommandRunner = run_command,
) -> dict[str, object]:
    """Run each verification command in `worktree` and report what happened.

    The deterministic phase's result (design §6) -- a plain dict, since it
    crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`).
    `runner` defaults to real execution and exists to be swapped in tests, the
    same callable-injection seam `worktree.py` uses for git.
    """
    worktree_path = str(worktree)
    result: dict[str, object] = {"passed": False, "verified": [], "detail": ""}
    verified: list[dict[str, object]] = result["verified"]  # type: ignore[assignment]

    for command in commands:
        argv = shlex.split(command) if isinstance(command, str) else [str(p) for p in command]
        try:
            completed = runner(argv, worktree_path)
        except (FileNotFoundError, PermissionError, NotADirectoryError) as exc:
            raise VerifyError(
                f"could not run {_display(command, argv)}: {exc}", argv=argv
            ) from exc

        if completed.exit_code == 0:
            verified.append(
                {
                    "command": command,
                    "ok": True,
                    "tail": plain_text(last_line(completed.stdout)),
                }
            )
            continue

        shown = _display(command, argv)
        diagnostic = command_diagnostic(
            completed.stdout,
            completed.stderr,
            f"{shown} exited with code {completed.exit_code}",
        )
        verified.append(
            {"command": command, "ok": False, "tail": plain_text(diagnostic)}
        )
        result["detail"] = plain_text(
            f"verification failed: {shown} — {diagnostic}", DETAIL_MAX
        )
        # Nothing is marked done after a red command (ship.mjs:89).
        return result

    result["passed"] = True
    return result
