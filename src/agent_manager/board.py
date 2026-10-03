"""The only caller of the `brd` CLI (design §4 line 119).

Six operations cross this seam: read one card, read a card subtree, read
every root of the board, write a card status, add a comment to a card, and
list a card's comments. Nothing about a *run* is ever written to the board
(decision D5, design §9) -- run state lives in agent-manager's own SQLite
projection and journal. Under decision B1 of the board-comments addendum the
board may also receive code-authored, append-only outcome comments, so
`set_status` and `comment_add` are the module's entire write surface. A
comment body is piped to `brd comment add <id> -` on stdin, never put in argv.

Status writes are serialized across threads and across `am` processes on one
project (spec X7 of the multi-process design): `set_status` runs under
`write_lock(repo_dir)`, the project's process-wide `board` lock, whose
in-process layer is the module-level `WRITE_LOCK`. `steps/rollup.py` holds the
same lock around its whole read-modify-write walk up a card's ancestors. Reads
and the comment calls take no lock here; the outbox flush that drives comments
owns its own locking (board-comments B7). A `locks.LockTimeoutError` is never
caught here.

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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent_manager import locks, models

M = TypeVar("M", bound=BaseModel)

BRD = "brd"
"""Executable name, resolved on PATH. Argv element zero of every call."""

WRITE_LOCK = threading.RLock()
"""The in-process layer of `write_lock`: serializes board writes across threads.

Held (through `write_lock`) for the whole of `set_status`, and by
`steps/rollup.py` around its entire ancestor walk, so a rollup's
read-modify-write is one critical section. Reentrant because the walk calls
`set_status` again on the same thread. Reads (`show`, `tree`, `roots`) do not
take it.
"""


def write_lock(repo_dir: Path | None) -> locks.ProcessLock:
    """The project's process-wide board write lock (spec X7).

    Keyed by the resolved `repo_dir`, or by the resolved working directory when
    it is `None` -- the directory `brd` resolves its board from. `WRITE_LOCK` is
    its in-process layer, read at call time, so threads of this process still
    serialize on it. Reentrant: a rollup's nested `set_status` takes no second
    flock.
    """
    root = Path(repo_dir).resolve() if repo_dir is not None else Path.cwd().resolve()
    return locks.project_lock(root, "board", local=WRITE_LOCK)


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


@dataclass(frozen=True)
class BoardComment:
    """One comment on a card, as `comment_list` reports it.

    A plain dataclass, not a Pydantic model: `comment_list` checks the three
    fields it keeps itself and drops brd's `entity_id` and `created_at`.
    """

    id: str
    body: str
    author: str


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


def comment_add_argv(card_id: str, author: str) -> list[str]:
    # The body never rides in argv: `-` makes brd read it from stdin, so a body
    # of any length or content is never a command-line argument.
    return [BRD, "comment", "add", card_id, "-", "--author", author]


def comment_list_argv(card_id: str) -> list[str]:
    return [BRD, "comment", "list", card_id]


def _run(
    argv: list[str], repo_dir: Path | None, input: str | None = None
) -> "subprocess.CompletedProcess[str]":
    """Run one `brd` argv list, returning the completed process.

    `input`, when given, is written to the process's stdin -- the only way a
    comment body reaches brd. Raises `BoardError` when `brd` is missing, or
    when it failed without printing an envelope -- a bare non-zero exit with
    stderr only.
    """
    try:
        completed = subprocess.run(
            argv,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            input=input,
        )
    except FileNotFoundError as exc:
        raise BoardError(
            f"could not run {argv[0]}: {exc.strerror}", argv=argv
        ) from exc

    if completed.returncode != 0 and not completed.stdout.strip():
        detail = completed.stderr.strip() or "no output"
        raise BoardError(detail, argv=argv, exit_code=completed.returncode)

    return completed


run_brd: Callable[
    [Sequence[str], Path | None, str | None], subprocess.CompletedProcess[str]
] = _run
"""The seam every public function runs `brd` through: `run_brd(argv, repo_dir, stdin)`.

Mirrors `GitRunner`/`run_git` in `steps/worktree.py` and
`CommandRunner`/`run_command` in `steps/verify.py`, but as a module global
rather than a parameter, so no public signature changes. Called positionally
-- `stdin` is the comment body for `comment_add` and `None` everywhere else --
and looked up at call time, so `monkeypatch.setattr(board, "run_brd", fake)`
takes effect. Whatever it returns still goes through `_decode` and
`_validated`, so a replacement's malformed envelope fails exactly as real brd's
would. Defaults to `_run`, which spawns the real binary.
"""


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
    completed = run_brd(argv, repo_dir, None)
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
    completed = run_brd(argv, repo_dir, None)
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
    completed = run_brd(argv, repo_dir, None)
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

    This is the module's only status writer. Under D5, as amended by decision
    B1 of the board-comments addendum, the board receives status transitions
    and code-authored, append-only outcome comments (`comment_add`) -- never
    run state, which lives in agent-manager's own store.

    Idempotent by construction (design §9 line 376): `brd update` stores the
    value it is given, so a repeat of the same transition is another successful
    write of the same value. Resume re-runs whole phases, and the phases that
    call this are `best_effort`, so a spurious second-call failure would be
    journalled as a board-write failure for work that actually succeeded.

    Runs entirely under `write_lock(repo_dir)`, so concurrent writers -- threads
    of this process or other `am` processes on the project -- reach brd one at a
    time. A `locks.LockTimeoutError` from the lock propagates uncaught.
    """
    argv = set_status_argv(card_id, status)
    with write_lock(repo_dir):
        completed = run_brd(argv, repo_dir, None)
        data = _decode(
            completed.stdout, argv=argv, exit_code=completed.returncode
        )
        return _validated(models.Card, data, argv=argv)


def comment_add(
    card_id: str,
    body: str,
    *,
    author: str = "am",
    repo_dir: Path | None = None,
) -> str:
    """Append one comment to a card via `brd comment add`, returning its id.

    The body is piped on stdin (`brd comment add <id> - --author <author>`),
    never placed in argv, so newlines, long text and shell metacharacters
    reach brd byte-for-byte and are never executed. No lock is taken and
    nothing is deduplicated: idempotence and serialization belong to the
    outbox flush that calls this (board-comments design B7). brd's own
    failures -- an unknown card, an empty body -- surface as `BoardError`.
    """
    argv = comment_add_argv(card_id, author)
    completed = run_brd(argv, repo_dir, body)
    data = _decode(completed.stdout, argv=argv, exit_code=completed.returncode)
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        raise BoardError(
            f"brd comment add {card_id} returned {type(data).__name__} where "
            "a comment object with a string id was expected",
            argv=argv,
            exit_code=completed.returncode,
        )
    return data["id"]


def comment_list(
    card_id: str, *, repo_dir: Path | None = None
) -> list[BoardComment]:
    """A card's comments, oldest first, via `brd comment list`.

    brd already lists oldest first (by creation time, then insertion), and that
    order is returned untouched -- nothing is re-sorted. Every comment on the
    card is included, whoever wrote it. A card with no comments is an empty
    list; an unknown card surfaces brd's `EntityNotFoundError` as `BoardError`.
    """
    argv = comment_list_argv(card_id)
    completed = run_brd(argv, repo_dir, None)
    data = _decode(completed.stdout, argv=argv, exit_code=completed.returncode)
    if not isinstance(data, list):
        raise BoardError(
            f"brd comment list {card_id} returned {type(data).__name__} where "
            "a list of comments was expected",
            argv=argv,
            exit_code=completed.returncode,
        )
    comments: list[BoardComment] = []
    for item in data:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(field), str) for field in ("id", "body", "author")
        ):
            raise BoardError(
                f"brd comment list {card_id} returned an item that is not a "
                f"comment with a string id, body and author: {item!r:.200}",
                argv=argv,
                exit_code=completed.returncode,
            )
        comments.append(
            BoardComment(id=item["id"], body=item["body"], author=item["author"])
        )
    return comments
