---
name: placement-boundaries
description: Audit that every module sits in its layer, imports only downward, keeps its libraries and processes confined, and keeps private names private — the failure class where code works from the wrong place and the next author copies the shortcut.
principle: Everything lives in the layer its role demands, and imports point strictly downward.
standards: [docs/standards/architecture.md]
checks:
  - {id: PB1, name: layer-direction, was: PB1, detection: mechanical, scopes: [diff, unit], applies-to: 'src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**', guard: pending}
  - {id: PB2, name: confinement, was: PB2, detection: mechanical, scopes: [diff, unit], applies-to: 'src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**', guard: pending}
  - {id: PB3, name: private-or-reexported-name, was: PB5, detection: mechanical, scopes: [diff, unit], applies-to: 'src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**', guard: pending}
  - {id: PB4, name: new-code-placement, detection: judgment, scopes: [diff], applies-to: 'src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
---

# placement-boundaries

The principle: every module lives in the layer its role demands, and a module
imports only modules in a strictly lower layer. The layer table is
`docs/standards/architecture.md §3` (`docs/standards/architecture.md §3.1` is
the target order with its band rules, `docs/standards/architecture.md §3.2`
places today's files on it); the import rules are
`docs/standards/architecture.md §4`; library and process confinement is
`docs/standards/architecture.md §5`; where new code goes is
`docs/standards/architecture.md §7`.

Why it matters: a misplaced import still runs, so nothing fails loudly. The cost
is that the layer table stops describing the code, and the next author copies
whichever shortcut they find first.

Distinct from neighbours: this axis asks "is it in the right module, and is the
dependency direction legal?". Whether two modules define the same thing is
single-source-of-truth; whether a docstring describes the module honestly is
honesty.

Known exceptions are listed in `docs/standards/architecture.md §11`. A
violation listed there is not a finding. A `docs/standards/architecture.md §11`
entry whose violation no longer
exists in the code is a finding (the list may only shrink, and the entry should
go). PB1-PB3 are decidable by an AST import graph and are slated for an
architecture test; until a guard exists they are audited by hand.

## Checks

### PB1 layer-direction

**Level:** anti-pattern
**Detection:** mechanical (guard: pending; an AST import graph over `src/agent_manager/`, checked against the layer table, decides it)

An import from a module to a module in the same or a higher layer breaks
`docs/standards/architecture.md §4` rule 1 and usually hides a cycle (rule 2).
Function-local and `TYPE_CHECKING` imports count like any other
(`docs/standards/architecture.md §4` rules 3-4). A band rule from
`docs/standards/architecture.md §3` broken by an import is the same finding:
Core importing an adapter, Steps importing `runtime`, Workflow importing
`runtime` or `dispatch`, Runtime importing a concrete workflow, Application
importing `typer` or `cli`.

**Detect:** for each file, list its `import agent_manager...` and
`from agent_manager... import` lines, including those inside functions and
under `if TYPE_CHECKING:` (`grep -n "agent_manager" <file>`). Look up the
importing and imported module's layer in `docs/standards/architecture.md §3`
(`docs/standards/architecture.md §3.2` for today's files, `docs/standards/architecture.md §3.1` for
modules that `docs/standards/architecture.md §6` has already created). Flag
any import whose target layer is not strictly lower. For a function-local
import, also flag a missing one-line reason comment directly above it
(`docs/standards/architecture.md §4` rule 3).

**Severity:** major for a new cycle or an upward module-level import; moderate
for a same-layer sibling import or a function-local import without its reason
comment.

**Carve-outs:** the entries in `docs/standards/architecture.md §11.1` and
`docs/standards/architecture.md §11.2`. A package `__init__.py` is L0 and is never the importer of anything.
A module that `docs/standards/architecture.md §6` splits is placed at the layer of its
highest part (`docs/standards/architecture.md §3.2`); do
not flag an import inside such a module merely because a future part would sit
lower.

### PB2 confinement

**Level:** anti-pattern
**Detection:** mechanical (guard: pending; per-library import allowlists plus string allowlists for `"brd"` argv heads, `datetime.now` and `mkdir`)

A library or process primitive used outside the one module that owns it
breaks `docs/standards/architecture.md §5`: the next change to that library's
use has two places to update, and the owning module's tests stop covering
every use.

**Detect:** grep the files under audit for each confined name and compare
against the table in `docs/standards/architecture.md §5`:
`grep -nE "^\s*(import|from) (pygents|grafo|typer|sqlite3|subprocess|fcntl)" <file>`,
`grep -nE "datetime\.now|\.mkdir\(|os\.fork|os\.setsid|Popen" <file>`, and a
read for a `brd` or `git` argv built outside `board.py` / `steps/worktree.py`
(rows 5.5 and 5.7) and SQL or transaction boundaries outside `store`
(row 5.4).

**Severity:** major for a new process spawn or SQL transaction outside its
owner; moderate for a new wall-clock read or hand-joined data path; minor for
an import used only as a type name.

**Carve-outs:** the "violated" rows of `docs/standards/architecture.md §5` and
the entries in `docs/standards/architecture.md §11.4`. `runtime/bridge.py`
may import `subprocess` to name `subprocess.Popen` as a type (row 5.13).

### PB3 private-or-reexported-name

**Level:** anti-pattern
**Detection:** mechanical (guard: pending; an `ImportFrom` or attribute read of a `_name` across modules, and any code in a package `__init__.py`)

A `_name` imported from, or read off, another module couples the caller to an
implementation detail its owner promised nothing about
(`docs/standards/architecture.md §4` rule 6, which holds inside one
sub-package too). A name imported so that callers can read it off the
importing module, or a package `__init__.py` that holds code, hides where the
name is defined (`docs/standards/architecture.md §4` rule 5).

**Detect:** `grep -nE "from agent_manager\S* import .*\b_[a-z]" <file>` and
`grep -nE "\b[a-z_]+\._[a-z]\w*" <file>` (read each hit: `self._x` and a
module's own private names are not hits). For re-exports, flag a module-level
`X = other.X` alias, an `import` whose name the importing module never uses
itself, and any statement other than a docstring or `__version__` in a package
`__init__.py`. Callers import leaf modules (`agent_manager.steps.worktree`, not
`agent_manager.steps`).

**Severity:** major for a new cross-module private import; moderate for a new
re-export alias; minor for a caller importing a package instead of its leaf
module.

**Carve-outs:** the entries in `docs/standards/architecture.md §11.3` and the
`cli.py` alias block in `docs/standards/architecture.md §11.4`. Tests reading a private name of the module
they test are out of scope for this check (test-quality TQ4 covers seams).

### PB4 new-code-placement

**Level:** principle
**Detection:** judgment

New code placed where `docs/standards/architecture.md §7` says it never goes —
business logic in `cli`, a gate with I/O, a `brd` call outside `board.py`, a
status value as a bare string, an exception class defined in `cli` — grows the
module that `docs/standards/architecture.md §6` is trying to split, and the next author copies it.

**Detect:** for each new function, class, constant or command in the diff,
name its kind from the rows of `docs/standards/architecture.md §7` and compare
its location with the "Goes in" column. Until the `docs/standards/architecture.md §6` split lands, read the
target column against today's file: new behaviour belongs in the module that
`docs/standards/architecture.md §6.1` or `docs/standards/architecture.md §6.2` names as its destination, or at least not in `cli.py` /
`orchestrate.py` when an existing lower module already owns that concern.

**Severity:** moderate for new business logic in `cli.py` or a new status,
exception or `brd` call outside its home; minor for a helper one module away
from its natural owner.

**Carve-outs:** a change that edits existing code in place in a module `docs/standards/architecture.md §6`
will split is not a finding; only new units are. A Typer command function in
`cli.py` that parses options and calls an existing use case is where commands
live today.

## Reporting

Report findings as `<ID> <file>:<line> — <what> → <fix recipe>`, most severe
first, citing the `docs/standards/architecture.md §N` rule that makes it a
violation. Name the `docs/standards/architecture.md §6.5` move that removes it when one does. If a check's
satisfaction could live outside the examined scope, say so instead of asserting
a violation.
