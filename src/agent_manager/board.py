"""The only caller of the `brd` CLI (design §4 line 119).

Three operations cross this seam: read one card, read a card subtree, write a
card status. Nothing about a *run* is ever written to the board (decision D5,
design §9) -- run state lives in agent-manager's own SQLite projection and
journal, so `set_status` is the module's entire write surface.

Every invocation is an argument list handed to `subprocess`. Design §5 line 252
is explicit that the program runs commands itself with argument lists, so
`shell_quote` from the shell-script original does not port and there is no
string to quote: a card id full of shell metacharacters is just one argv
element.

Naming, slugs, branches and ref matching are `dag.py`'s job, not this module's.
"""

import subprocess
from pathlib import Path

BRD = "brd"
"""Executable name, resolved on PATH. Argv element zero of every call."""


class BoardError(RuntimeError):
    """Any failure of a `brd` invocation, carrying enough to journal it.

    A caller running a `best_effort: true` phase records this and continues;
    tolerance is the engine's policy, never the adapter's, so nothing here is
    swallowed.
    """

    def __init__(
        self,
        message: str,
        *,
        argv: list[str],
        exit_code: int | None = None,
        error_type: str | None = None,
    ) -> None:
        self.message = message
        self.argv = list(argv)
        self.exit_code = exit_code
        self.error_type = error_type
        super().__init__(
            f"{message} (argv={self.argv!r}, exit_code={exit_code!r})"
        )


def show_argv(card_id: str) -> list[str]:
    return [BRD, "show", card_id]


def tree_argv(card_id: str) -> list[str]:
    return [BRD, "tree", card_id]


def set_status_argv(card_id: str, status: str) -> list[str]:
    # brd has no set-status subcommand: `update --status` is the only writer
    # (design §17 line 520 names `brd update` directly).
    return [BRD, "update", card_id, "--status", status]


def _run(
    argv: list[str], repo_dir: Path | None
) -> "subprocess.CompletedProcess[str]":
    """Run one `brd` argv list, returning the completed process.

    Raises `BoardError` when `brd` is missing, or when it failed without
    printing an envelope -- a bare non-zero exit with stderr only.
    """
    try:
        completed = subprocess.run(
            argv,
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise BoardError(
            f"could not run {argv[0]}: {exc.strerror}", argv=argv
        ) from exc

    if completed.returncode != 0 and not completed.stdout.strip():
        detail = completed.stderr.strip() or "no output"
        raise BoardError(detail, argv=argv, exit_code=completed.returncode)

    return completed
