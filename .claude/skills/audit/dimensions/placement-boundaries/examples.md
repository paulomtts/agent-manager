# placement-boundaries — examples

Every example is real code at `master` `26cca95`. Line numbers drift; re-read
before relying on one. The PB1-PB3 bad examples are known exceptions listed in
`docs/standards/architecture.md §11`, so on a real run they are not reported;
they are here to show the shape a new violation has.

## PB1 layer-direction

Bad (`src/agent_manager/orchestrate.py:61-65`): an Application module imports
the Interface module at module level, which is upward and closes a cycle
(`cli` also imports `orchestrate`).

```python
from agent_manager import (
    bases,
    board,
    census,
    cli,
```

Good (`src/agent_manager/steps/rollup.py:38-39`): a Steps module (L9) imports an
adapter (`board`, L8) and a Core module (`census`, L1), both strictly lower.

```python
from agent_manager import board
from agent_manager.census import (
```

## PB2 confinement

Bad (`src/agent_manager/cli.py:24`): the Interface module imports `sqlite3`,
which only `store` may import (`docs/standards/architecture.md §5`, row 5.4).

```python
import sqlite3
```

Good (`src/agent_manager/board.py:44-45`): the `brd` executable name is defined
once, in the one module that builds a `brd` argv (row 5.5).

```python
BRD = "brd"
"""Executable name, resolved on PATH. Argv element zero of every call."""
```

## PB3 private-or-reexported-name

Bad (`src/agent_manager/bases.py:40`): a private helper read across modules
(`docs/standards/architecture.md §4`, rule 6).

```python
from agent_manager.steps.integrate import MergeInProgressError, _ref_exists, merge_tip
```

Good (`src/agent_manager/bases.py:41`): public names imported from the leaf
module that defines them.

```python
from agent_manager.steps.worktree import GitError, ensure, run_git
```

## PB4 new-code-placement

Bad (`src/agent_manager/cli.py:808-818`): a run-status decision lives in the
Typer module. `docs/standards/architecture.md §6.1` moves it to `card_run`;
new logic of this kind added to `cli.py` today is a finding.

```python
def card_run_status(summary: SubtaskSummary, stop: StopSignal) -> str:
    ...
    if stop.requested == "cancel":
        return "cancelled"
    return summary.status
```

Good: a new pure gate goes in `src/agent_manager/steps/reducers.py`
(`docs/standards/architecture.md §7`, row "Gate"), and a new `brd` call goes in
`src/agent_manager/board.py` (row "`brd` interaction").
