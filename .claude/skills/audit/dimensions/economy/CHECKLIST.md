---
name: economy
description: Audit that code spends no more than the behavior costs — the failure class where excess machinery (dead logic, redundant work, hollow wiring, speculative abstraction) survives review because it works.
principle: Code costs no more than the behavior it delivers; every moving part earns its place.
standards: [docs/standards/architecture.md]
checks:
  - {id: EC1, name: dead-logic, was: EC1, detection: judgment, scopes: [diff, unit], applies-to: 'src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
  - {id: EC2, name: redundant-work, was: EC2, detection: judgment, scopes: [diff, unit], applies-to: 'src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
  - {id: EC3, name: hollow-indirection, was: EC3, detection: judgment, scopes: [diff, unit], applies-to: 'src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
  - {id: EC4, name: speculative-generality, was: EC4, detection: judgment, scopes: [diff, unit], applies-to: 'src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
---

Principle: code costs no more than the behavior it delivers.

Needless complexity is one disease with two symptoms: efficiency waste (logic
that runs but buys nothing) and readability waste (structure a reader must
traverse but that carries nothing). The audit asks of every unit under audit:
could the same behavior exist with less machinery, and does every moving part
earn its place? This is not honesty's territory (comments, docstrings,
citations) and not single-source-of-truth's (the same thing defined twice) — it
is the machinery itself.

One rule governs the whole axis: indirection the architecture asks for is never
a finding. This codebase deliberately buys some — injected seams
(`docs/standards/architecture.md §5`, row 5.14), frozen contracts
(`docs/standards/architecture.md §10`), the harness Protocol and registry
(`docs/standards/architecture.md §7`) — and each check below names those
carve-outs. What nothing sanctions is fair game.

## Checks

### EC1 dead-logic

**Level:** anti-pattern. **Detection:** judgment.

A branch that cannot fire, a parameter no caller varies, a default never
overridden, or a guard duplicating an upstream guarantee makes the reader
model states the system can't reach — every future change pays rent on a path
that never executes.

**Detect:** for each conditional and parameter under audit, trace the actual
call sites (`grep -rn "<name>(" src/ tests/`); prove the branch reachable and
the parameter varied, or flag. A guard is dead when the guarded condition is
already guaranteed by every path into the function.

**Severity:** major for an unreachable branch or a dead parameter on a public
function; moderate for a guard re-checking an upstream guarantee; minor for a
never-overridden default.

**Carve-outs:** validation at a process boundary is the boundary's job, not
dead logic: a Pydantic model parsing a harness result file, `board.py` parsing
`brd` output, the journal reader rejecting unknown fields. A reader that
accepts both `canceled` and `cancelled` is required by
`docs/standards/architecture.md §9` rule 2, and the camelCase/snake_case dual
read in `steps/reducers.py` is frozen by `docs/standards/architecture.md §10`
rule 5. A parameter that only tests vary (a `now`, a `runner_factory`, a
`run_git`, a `board_api`) is a test seam per
`docs/standards/architecture.md §5`, not a dead parameter. A
`typing.assert_never` closing a dispatch over a closed union is not
unreachable code.

### EC2 redundant-work

**Level:** anti-pattern. **Detection:** judgment.

Recomputing what the caller already holds, reading the same journal, row or
`brd` card twice, transforming then un-transforming, or doing loop-invariant
work inside the loop spends real cycles to produce a value the scope already
contained — and signals to the reader a data dependency that doesn't exist.

**Detect:** data-flow walk of each function under audit: trace every derived
value back to whether an equivalent already existed in scope, and every
subprocess call (`brd`, `git`) or store read to whether the same data was
already in hand.

**Severity:** major for a duplicate `brd` or `git` subprocess or a repeated
full journal replay on a hot path (a poll loop, a per-subtask step); moderate
for repeated in-memory recomputation inside a loop; minor for cold-path double
work.

**Carve-outs:** a re-read after a write or after a wait is deliberate — `brd`
and the store change underneath a running process (another lane, a human, a
detached child), so a fresh read before a decision is correctness, not
redundancy. Reading the journal line and then the row in one write path
follows the write order frozen by `docs/standards/architecture.md §10` rule 4.

### EC3 hollow-indirection

**Level:** principle. **Detection:** judgment.

A function, class, or module with one caller that adds no semantics — no
narrowing, no invariant, no name saying more than its callee — makes every
reader traverse a hop that carries nothing. At module scale the same disease
reads as wiring longer than payload: more code routing the work than doing it.

**Detect:** for each callable under audit, count its callers, then state in
one sentence what it adds beyond its callee. If the sentence cannot be
written, flag it.

**Severity:** major for a new module or class of pass-throughs, or wiring
exceeding payload at module scale; moderate for a single hollow wrapper; minor
for a trivial lambda or alias.

**Carve-outs:** a `RunnerFactory`, `Collaborators` or other seam from
`docs/standards/architecture.md §5` (row 5.14) exists so a test can substitute
it; one production implementation is its normal state. A `harness/<name>.py`
adapter registered in `harness/registry.py` follows
`docs/standards/architecture.md §7`. An async wrapper that runs a sync call
through `asyncio.to_thread` adds a real property (it does not block the loop).
A step, gate or `when` predicate referenced by a shipped workflow cannot be
inlined without changing the workflow digest
(`docs/standards/architecture.md §10` rule 3); report it as a note, never as a
fix to apply. The `cli.py` alias block is a known exception
(`docs/standards/architecture.md §11`), owned by placement-boundaries.

### EC4 speculative-generality

**Level:** principle. **Detection:** judgment.

An axis of variation with no second user — config nothing sets, an abstract
base with one subclass, a registry with one entry, a parameter plumbed through
N layers to a constant — charges every reader for flexibility nobody bought.

**Detect:** for each axis of variation under audit, grep `src/` and `tests/`
for a second concrete use. None, and no `docs/standards/**` section naming an
imminent one, is a finding.

**Severity:** major for a new abstraction layer serving an imaginary case;
moderate for dead config or a flag nothing reads; minor for an over-general
signature.

**Carve-outs:** a Protocol whose second implementation is a test fake has two
users; the fake counts (`FakeLauncher`, `FakeDriver`, `FakeBoard`). The
harness registry is the extension point `docs/standards/architecture.md §7`
names for a new harness. A workflow is declared data interpreted by the
runtime (`docs/standards/architecture.md §2`); a phase option used by only one
shipped workflow is still part of the workflow language.

## Reporting

Report findings as `<ID> <file>:<line> — <what> → <fix recipe>`, most severe
first. Cite the rule that sanctions or condemns the pattern. If a check's
satisfaction could live outside the examined scope — a second caller in an
unexamined module, a variation axis used elsewhere — say so instead of
asserting a violation.
