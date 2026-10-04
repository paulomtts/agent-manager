---
name: honesty
description: Audit that docstrings, comments, docs and citations say what the code actually does, and say only the contract — the failure class where prose silently stops matching the thing it describes.
principle: Anything that describes the code (docstrings, comments, docs, cites) must say what the code actually does.
standards: [docs/standards/architecture.md]
checks:
  - {id: HO1, name: claim-drift, was: HO1, detection: judgment, scopes: [diff, unit], applies-to: 'src/agent_manager/**/*.py, README.md, CLAUDE.md, docs/standards/**, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
  - {id: HO2, name: narrative-docstring, was: HO2, detection: judgment, scopes: [diff, unit], applies-to: 'src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
  - {id: HO3, name: citation-rot, was: HO3, detection: judgment, scopes: [diff, unit], applies-to: 'README.md, CLAUDE.md, docs/standards/**, src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
---

Principle: what describes the code must say what the code does.

Names, signatures and types are the load-bearing surface; docstrings,
comments, the README, `CLAUDE.md` and the standards are a read-replica of that
surface with no compiler holding them in sync. This axis checks the replica
against the source: a docstring promising what its module does not do, a
docstring narrating history instead of stating a contract, a cite that no
longer resolves. It is distinct from economy (excess machinery) and from
single-source-of-truth (a contract restated in two places): honesty is about
the description matching the behavior, whichever side is stale.

The house rule for docstrings and comments is
`docs/standards/architecture.md §8`: a docstring states the contract, a comment
explains only a non-obvious why, and neither carries narrative.

Specs and plans under `docs/superpowers/` are local, untracked history
(`docs/standards/architecture.md §1`). They are never audited, and a claim is
never checked against them.

## Checks

### HO1 claim-drift

**Level:** principle. **Detection:** judgment.

A docstring, README section, `CLAUDE.md` rule or standards rule that claims
something the code does not do — a module docstring promising "decides
nothing" above a module that decides, a function docstring naming a raise that
never happens, a README table listing a key the code never writes — means the
next reader trusts a description that is false.

**Detect:** for each module under audit, read its module docstring and the
docstrings of its public functions and classes, and check each concrete claim
(what it owns, what it never does, what it returns, what it raises, which
module it delegates to) against the code. For README, `CLAUDE.md` and
`docs/standards/**`, grep the symbol, command, key or path each claim names and
confirm the code produces and consumes it on a live path.

**Severity:** major when the claim is about a boundary or contract a reader
would rely on (what a module never does, an envelope or exit code, a raise);
moderate for a stale behavioral description; minor for prose that is merely
dated with no behavioral gap.

**Carve-outs:** a rule in `docs/standards/architecture.md` that describes a
target state (`docs/standards/architecture.md §6` destinations, `docs/standards/architecture.md §9`
migration steps) is a plan, not a claim
about today's code; a known exception listed in
`docs/standards/architecture.md §11` is the standard admitting the gap. When
code and doc disagree, name which side you believe is stale rather than
assuming the code is always wrong.

### HO2 narrative-docstring

**Level:** anti-pattern. **Detection:** judgment (mechanical pre-filter: `grep -nE '§|\bD[0-9]+\b|\bS[0-9]+\b|\bT[0-9]+\b|card [0-9a-f]{8}|[Pp]orted from|addendum|design spec|docs/superpowers' <file>` enumerates most candidates; whether the rest of a docstring is narrative is a reading call).

`docs/standards/architecture.md §8` rule 1: a docstring states the contract —
what the function does, its inputs and outputs, its invariants, what it
raises; a module docstring says what the module owns, in a few lines. Rule 2: a
comment explains only a non-obvious why, in one or two lines. Rule 3: no
narrative — no spec or section citations (`§`, `D5`, `S1`, `T2`), no card ids,
no "ported from X", no history, no rationale walls. A docstring or comment that
breaks these drifts the moment the code changes, and buries the contract a
caller needs under the story of how it was written.

**Detect:** run the pre-filter grep over the files under audit, then read every
module docstring, every public function or class docstring, and every comment
block of three or more lines. Flag: a citation of a spec, plan, section,
decision id or card id; history ("was", "used to", "now", "no longer", "after
the X refactor"); a rationale wall longer than the contract it explains; a
comment restating the next line of code; a docstring longer than the function
it documents without stating a contract the signature cannot express.

**Severity:** moderate for a docstring that cites a spec, section, card or
decision id, or that narrates history; minor for a single-line restatement or
an over-long rationale on otherwise correct prose.

**Carve-outs:** directives are skipped outright (`# noqa`, `# type: ignore`,
`# pragma: no cover`, shebangs, encoding lines). A one-line comment above a
function-local import stating its reason is required by
`docs/standards/architecture.md §4` rule 3. A comment explaining a non-obvious
why in one or two lines is the rule, not a violation. The existing narrative
style is legacy and is removed when its module is touched for another reason,
not in one sweep (`docs/standards/architecture.md §8` rule 4): on a unit
scope, report it with the fix recipe "rewrite as contract when the module is
next touched", never as a sweep to schedule.

### HO3 citation-rot

**Level:** anti-pattern. **Detection:** judgment (mechanical pre-filter: path-shaped tokens are greppable; whether a cited line, symbol or section still matches needs a read).

A `file:line` cite, a backtick-quoted symbol, a `§N` cross-reference or a
relative link that no longer resolves sends a reader hunting for something that
moved, was renamed or was deleted. A cite into `docs/superpowers/` from a
tracked document points at local history that a fresh clone does not have
(`docs/standards/architecture.md §1`), so it never resolves for a reader.

**Detect:** extract every citation-shaped token from README.md, CLAUDE.md,
`docs/standards/**` and the docstrings and comments of
`src/agent_manager/**` under audit — `path:line` and `path:start-end` patterns,
backtick-quoted paths and `module.symbol` names, `<doc>.md §N` cross-refs, and
`[text](relative/path.md#anchor)` links — then resolve each: is the path
tracked (`git ls-files <path>`), is the line in range and still showing what
the cite claims, does the symbol grep-hit somewhere live, does the section
number's heading still match, does the anchor slugify to a real heading. A
cite whose target is under `docs/superpowers/` is a finding without resolving
it.

**Severity:** major when the citation names a source of truth that is untracked
or gone (a doc under `docs/superpowers/`, a deleted file, a renumbered section
that now means something else); moderate when the target exists but the line
or symbol drifted; minor when the drift is cosmetic.

**Carve-outs:** a document naming a `docs/superpowers/` path as data — the
default spec and plan paths the `task` workflow writes, a `.gitignore`
example — is describing a feature, not citing history. The `file:line` cites in
`docs/standards/architecture.md` are measured at the commit `docs/standards/architecture.md §1` names; check
them at that commit (`git show <commit>:<path>`), and flag only a cite that is
wrong there or whose `docs/standards/architecture.md §11` entry no longer has a violation behind it. A document
that defines a path it is itself creating is not drift. A docstring cite that
is also narrative is reported once, under HO2.

## Reporting

Report findings as `<ID> <file>:<line> — <what> → <fix recipe>`, most severe
first, citing the `docs/standards/architecture.md §N` rule that makes it a
violation. For HO1 say which side (code or prose) you believe is stale. If a
check's satisfaction could live outside the examined scope, say so instead of
asserting a violation.
