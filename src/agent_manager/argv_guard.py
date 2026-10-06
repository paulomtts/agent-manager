"""Re-exec `am run` and `am resume` with no verification text in argv (card 1b938053).

`pkill -f "<command>"` run by an agent matches every process whose command
line holds that text, the engine included. `neutralize` moves each `--verify`
value into the `AM_VERIFY_JSON` environment variable and puts the hidden
`--verify-from-env` flag in their place; every other token stays, identity
options included. `reexec_neutral` execs the interpreter on that argv.
This module imports only the stdlib, and it is the only module that calls
`os.execve` (docs/standards/architecture.md §5 row 5.15).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

VERIFY_ENV = "AM_VERIFY_JSON"
"""Carries the verification commands across the re-exec, as a JSON list of strings."""

FROM_ENV_FLAG = "--verify-from-env"
"""The hidden `run`/`resume` flag that reads the commands from `VERIFY_ENV`."""

VERIFY_FLAG = "--verify"
"""The option whose values leave argv."""

COMMANDS = ("run", "resume")
"""The subcommands that take `--verify`; argv for any other is left alone."""


def neutralize(
    argv: Sequence[str], environ: Mapping[str, str]
) -> tuple[list[str], dict[str, str]] | None:
    """The neutral argv and environment for `argv`, or `None` to leave the process alone.

    `argv[0]` is the program. `None` unless `argv[1]` is in `COMMANDS` and at
    least one `--verify X` or `--verify=X` comes before any `--`; also `None`
    when `--verify-from-env` is already there, or a bare `--verify` has no
    value. Otherwise every `--verify` is removed, `--verify-from-env` is
    inserted at index 2, every other token keeps its order, and the returned
    environment is a copy of `environ` with `AM_VERIFY_JSON` set to the JSON
    list of the values in command-line order. Neither input is mutated.
    """
    if len(argv) < 2 or argv[1] not in COMMANDS:
        return None
    end = argv.index("--") if "--" in argv else len(argv)
    if FROM_ENV_FLAG in argv[2:end]:
        return None
    kept: list[str] = []
    values: list[str] = []
    index = 2
    while index < end:
        token = argv[index]
        if token == VERIFY_FLAG:
            if index + 1 == end:
                return None
            # Click gives a bare option the next token whatever it looks like.
            values.append(argv[index + 1])
            index += 2
            continue
        if token.startswith(VERIFY_FLAG + "="):
            values.append(token.partition("=")[2])
        else:
            kept.append(token)
        index += 1
    if not values:
        return None
    new_argv = [argv[0], argv[1], FROM_ENV_FLAG, *kept, *argv[end:]]
    return new_argv, {**environ, VERIFY_ENV: json.dumps(values)}
