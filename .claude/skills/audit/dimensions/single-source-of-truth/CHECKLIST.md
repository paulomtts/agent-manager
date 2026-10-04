---
name: single-source-of-truth
description: Audit that each status, constant, default, gate and contract is defined once and read from there — the failure class where every copy works, so review never notices there are copies until one changes.
principle: Define each fact once, in the module that owns it, and derive or import it everywhere else.
standards: [docs/standards/architecture.md, README.md]
checks:
  - {id: SS1, name: status-vocabulary, detection: judgment, scopes: [diff, unit], applies-to: 'src/agent_manager/**/*.py, README.md, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
  - {id: SS2, name: duplicated-definition, detection: judgment, scopes: [diff, unit], applies-to: 'src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
  - {id: SS3, name: derived-fact-stored-twice, detection: judgment, scopes: [diff, unit], applies-to: 'src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
  - {id: SS4, name: restated-contract-drift, detection: judgment, scopes: [diff, unit], applies-to: 'README.md, CLAUDE.md, src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
---

# single-source-of-truth

**Principle.** Define each fact once, in the module that owns it, and derive or
import it everywhere else. A duplicate that works is the hardest defect to see:
every copy passes review, the tests stay green, and the cost lands later, when
one copy changes and the others silently do not.

**Why it matters here.** `agent-manager` writes an append-only journal, a
SQLite projection, envelopes and board statuses that other tools read. A
status spelled two ways, a timeout defined twice, or a README table that
restates a code contract are each a place where the shipped contract can fork
without any test noticing. Every finding here names the one place the fact
should live and the copies that should read from it.

**Neighbouring axes.** economy owns dead or redundant machinery; a duplicate is
not an SS finding merely because it is also waste. honesty owns whether a
description matches the code; SS4 owns only a contract that is *defined* in
two places (code and README) and must stay identical. placement-boundaries owns
which module a definition belongs in by layer; SS owns whether there is exactly
one.

## Checks

### SS1 status-vocabulary

**Level:** anti-pattern
**Detection:** judgment (mechanical pre-filter: `grep -rnE '"cancell?ed"' src/` and `grep -rnE '(== |in \(|in \{)"(pending|started|done|failed|escalated|stopped|cancell?ed)"' src/` enumerate candidates; whether a literal is a status write, a grouping, or a read of a typed field is a reading call)

A run, story, subtask or phase status is a value of `models.Status`, and a new
status value goes there plus the README journal table
(`docs/standards/architecture.md §7`, row "Status value"). The canonical
spelling is `canceled`, matching `brd`; new code writes only `canceled`, and
readers accept both spellings forever (`docs/standards/architecture.md §9`
rules 1-2). A status written as a bare string the Literal does not contain, a
new write of `cancelled`, or a reader that matches only one spelling forks the
vocabulary that the journal, the store and the plugin all read.

**Detect:** run the pre-filter greps over the files under audit. For each hit,
decide which it is: a write of a status (to the store, the journal, an
envelope, a comment key), a status grouping (a tuple or set of statuses such
as "terminal" or "resumable"), or a comparison against a `Status`-typed field.
Flag a write or a grouping whose value is not in `models.Status`; a new write
of `cancelled`; a reader comparing against exactly one of the two spellings; a
status grouping defined inline when the same grouping exists elsewhere.

**Severity:** major for a new journal, row or envelope write that forks the
spelling, or a reader that drops one spelling (a stored `cancelled` run would
be misread); moderate for an inline status grouping duplicated across
modules; minor for a comment or docstring spelling.

**Carve-outs:** the sites `docs/standards/architecture.md §9` rule 3 enumerates
are the migration's own work list and are not new findings until that
migration lands; report a site missing from that list. The comment idempotency
key in `comments.py` keeps `cancelled` on purpose
(`docs/standards/architecture.md §9` rule 3). `brd` card statuses
(`census.FINISHED_STATUSES`, `census.OUT_OF_PLAY_STATUSES`) are a separate
vocabulary owned by `census`; a comparison against them is not a run status.
English prose ("the task was cancelled") is not a status value.

### SS2 duplicated-definition

**Level:** anti-pattern
**Detection:** judgment (mechanical pre-filter: `grep -rn "def <name>" src/` for a helper name, and `grep -rnE "[0-9]+\.[0-9]+|[0-9]{3,}" src/` for repeated numeric literals)

The same helper, constant, default or gate defined in two or more modules — a
private `_utcnow` per module, a timeout literal written in two places, two
functions deciding the same predicate — means a change lands in one copy and
not the others. `docs/standards/architecture.md §5` names some of these with
their one home (row 5.12, the wall clock in `clock`), and
`docs/standards/architecture.md §6.5` names the moves that collapse them (M1
for the timeout, M3 for the clock).

**Detect:** for each top-level function, constant and default value under
audit, grep `src/agent_manager/` for another definition with the same name,
the same literal value used for the same meaning, or the same body under a
different name. A copy is a finding when both copies mean the same thing (a
change to one would have to be repeated in the other), not merely when two
literals happen to be equal.

**Severity:** major for a duplicated default or constant that is part of a
frozen contract (`docs/standards/architecture.md §10`: a timeout in the workflow
digest, an envelope key); moderate for a duplicated helper or gate; minor for
a duplicated small pure helper with no behavior beyond the standard library.

**Carve-outs:** the copies listed in `docs/standards/architecture.md §5` row
5.12 and in `docs/standards/architecture.md §11` are known; report them only on
a unit scope, once each, with the `docs/standards/architecture.md §6.5` move that removes them as the fix
recipe, and report any copy not on that list as new. Two modules defining
the same name for different meanings is a naming question, not a duplicate.

### SS3 derived-fact-stored-twice

**Level:** principle
**Detection:** judgment

A fact derivable from another fact, but written down as its own literal — a
status set that should be the union or difference of two others, a run's
outcome stored in a field and also recomputed from its rows by a reader, a
count kept next to the list it counts — drifts the first time the source
changes and the derived copy is not updated.

**Detect:** for each set, mapping, counter or stored field under audit, ask
whether it can be computed from something already defined or stored. Grep for
the source it would derive from (`census` status sets, `models.Status`, the
journal event that carries the same fact). Flag a literal that restates a
derivable value, and a stored field that a reader also recomputes from rows
without the two being checked against each other.

**Severity:** major when the two copies feed different readers of a frozen
contract (the journal and the SQLite projection, an envelope and the store);
moderate for an in-process set or constant that restates another; minor for a
local variable.

**Carve-outs:** the SQLite projection restating the journal is the design
(`docs/standards/architecture.md §2`, truth and projection), kept consistent by
replay and the divergence check in `store`; only a projection column with no
replay path is a finding. A cache with an explicit invalidation is not a
second source.

### SS4 restated-contract-drift

**Level:** principle
**Detection:** judgment

The README restates contracts the code defines: the journal event table, the
status values, the envelope and report shapes, exit codes, the `am watch`
hello line, the data-dir layout (`docs/standards/architecture.md §10`). A
restated contract that disagrees with the code misleads every external reader,
and those readers (the plugin, scripts) code against the README.

**Detect:** for each README table or code block that lists keys, statuses,
event kinds, exit codes or paths, find the code that defines the same thing
(`models`, `store`, `cli`, `paths`) and diff the two lists. Read
`tests/test_readme.py` first: a fact it already pins is guarded, so report only
the facts it does not check.

**Severity:** major for a README entry that contradicts the code on a frozen
contract (a missing or extra key, a wrong exit code, a wrong status spelling);
moderate for a README table missing an additive key the code now writes;
minor for wording drift that does not change a value.

**Carve-outs:** the README note that older journals carry `cancelled` is
required by `docs/standards/architecture.md §9` rule 3, not a contradiction.
A README section that describes a deferred feature as not existing is a claim
(honesty HO1), not a restated contract.

## Reporting

Report findings as `<ID> <file>:<line> — <what> → <fix recipe>`, most severe
first. Every fix recipe names the one place the fact should live and every
copy that should read from it. Cite the `docs/standards/architecture.md §N`
rule that names the owner when there is one. If a check's satisfaction could
live outside the examined scope — a copy in an unexamined module — say so
instead of asserting a violation.
