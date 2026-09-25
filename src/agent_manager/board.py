"""The only caller of the `brd` CLI (design §4 line 119).

Four operations cross this seam: read one card, read a card subtree, read
every root of the board, write a card status. Nothing about a *run* is ever
written to the board (decision D5, design §9) -- run state lives in
agent-manager's own SQLite projection and journal, so `set_status` is the
module's entire write surface.

Writes are serialized within one process: `set_status` runs under the
module-level `WRITE_LOCK`, a reentrant lock that `steps/rollup.py` also holds
around its whole read-modify-write walk up a card's ancestors. Reads take no
lock. Nothing here coordinates two separate `am` processes on one repository;
that is not supported.

Every invocation is an argument list handed to `subprocess`. Design §5 line 252
is explicit that the program runs commands itself with argument lists, so
`shell_quote` from the shell-script original does not port and there is no
string to quote: a card id full of shell metacharacters is just one argv
element.

Naming, slugs, branches and ref matching are `dag.py`'s job, not this module's.
"""

import json
import subprocess
import threading
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent_manager import models

M = TypeVar("M", bound=BaseModel)

BRD = "brd"
"""Executable name, resolved on PATH. Argv element zero of every call."""

WRITE_LOCK = threading.RLock()
"""Serializes board writes across threads of one process.

Held for the whole of `set_status`, and by `steps/rollup.py` around its entire
ancestor walk, so a rollup's read-modify-write is one critical section.
Reentrant because the walk calls `set_status` again on the same thread. Reads
(`show`, `tree`, `roots`) do not take it.
"""


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


def roots_argv() -> list[str]:
    # No id: brd answers with every top-level card, descendants nested.
    return [BRD, "tree"]


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


def _decode(stdout: str, *, argv: list[str], exit_code: int) -> object:
    """Pull `data` out of a `{"ok", "data"}` envelope, or raise `BoardError`.

    brd's failure shape is `{"ok": false, "error": {"type", "message"}}` and its
    message is surfaced verbatim: this module invents no wording and no
    "not found" semantics of its own.
    """
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BoardError(
            f"brd printed output that is not JSON: {stdout.strip()[:200]!r}",
            argv=argv,
            exit_code=exit_code,
        ) from exc

    if not isinstance(envelope, dict) or "ok" not in envelope:
        raise BoardError(
            f"brd printed JSON that is not an {{ok, data}} envelope: "
            f"{stdout.strip()[:200]!r}",
            argv=argv,
            exit_code=exit_code,
        )

    if not envelope["ok"]:
        error = envelope.get("error")
        if not isinstance(error, dict):
            error = {}
        raise BoardError(
            error.get("message", "brd reported a failure with no message"),
            argv=argv,
            exit_code=exit_code,
            error_type=error.get("type"),
        )

    if exit_code != 0:
        raise BoardError(
            f"brd exited {exit_code} despite printing an ok envelope",
            argv=argv,
            exit_code=exit_code,
        )

    if "data" not in envelope:
        raise BoardError(
            "brd printed an ok envelope with no data",
            argv=argv,
            exit_code=exit_code,
        )

    return envelope["data"]


def _validated(model: type[M], data: object, *, argv: list[str]) -> M:
    """Validate brd's payload at the process boundary (CLAUDE.md convention)."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise BoardError(
            f"brd's payload did not validate as {model.__name__}: {exc}",
            argv=argv,
            exit_code=0,
        ) from exc


def show(card_id: str, *, repo_dir: Path | None = None) -> models.Card:
    """One card, via `brd show`.

    `repo_dir` is the directory brd runs in; it resolves its board from the
    nearest `.brd` marker at or above that directory. Nothing is cached here --
    the engine caches the card at run start (design §7 line 287).
    """
    argv = show_argv(card_id)
    completed = _run(argv, repo_dir)
    data = _decode(
        completed.stdout, argv=argv, exit_code=completed.returncode
    )
    return _validated(models.Card, data, argv=argv)


def tree(card_id: str, *, repo_dir: Path | None = None) -> models.CardNode:
    """A card and its descendants, via `brd tree`.

    brd's `build_tree` always answers with a list; rooted at one card id that
    list holds exactly one node, which is what callers want. Ordering and depth
    come from brd -- nothing is re-sorted or re-parented here.
    """
    argv = tree_argv(card_id)
    completed = _run(argv, repo_dir)
    data = _decode(completed.stdout, argv=argv, exit_code=completed.returncode)
    if not isinstance(data, list) or len(data) != 1:
        found = len(data) if isinstance(data, list) else type(data).__name__
        raise BoardError(
            f"brd tree {card_id} returned {found} where exactly one root was "
            "expected",
            argv=argv,
            exit_code=completed.returncode,
        )
    return _validated(models.CardNode, data[0], argv=argv)


def roots(*, repo_dir: Path | None = None) -> list[models.CardNode]:
    """Every top-level card on the board, via `brd tree` with no id.

    Each root nests its descendants under `children`. An empty board is an
    empty list, not an error. Ordering and depth come from brd -- nothing is
    re-sorted or re-parented here.
    """
    argv = roots_argv()
    completed = _run(argv, repo_dir)
    data = _decode(completed.stdout, argv=argv, exit_code=completed.returncode)
    if not isinstance(data, list):
        raise BoardError(
            f"brd tree returned {type(data).__name__} where a list of roots "
            "was expected",
            argv=argv,
            exit_code=completed.returncode,
        )
    return [_validated(models.CardNode, item, argv=argv) for item in data]


def set_status(
    card_id: str, status: str, *, repo_dir: Path | None = None
) -> models.Card:
    """Write a card's board status via `brd update --status`, and return it.

    This is the module's entire write surface: under D5 the board receives
    status transitions and nothing else, and run state lives in agent-manager's
    own store.

    Idempotent by construction (design §9 line 376): `brd update` stores the
    value it is given, so a repeat of the same transition is another successful
    write of the same value. Resume re-runs whole phases, and the phases that
    call this are `best_effort`, so a spurious second-call failure would be
    journalled as a board-write failure for work that actually succeeded.

    Runs entirely under `WRITE_LOCK`, so concurrent writers in one process
    reach brd one at a time.
    """
    argv = set_status_argv(card_id, status)
    with WRITE_LOCK:
        completed = _run(argv, repo_dir)
        data = _decode(
            completed.stdout, argv=argv, exit_code=completed.returncode
        )
        return _validated(models.Card, data, argv=argv)
