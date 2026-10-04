---
name: test-quality
description: Audit that tests sit in the tier their spawns demand, prove behavior and fail for a reason, substitute collaborators through the injected seams, and share their fakes — and that new production behavior has a test.
principle: A test earns its place only by proving real behavior at the cheapest tier that can prove it, and failing only when that behavior breaks.
standards: [CLAUDE.md, docs/standards/architecture.md]
checks:
  - {id: TQ1, name: wrong-tier, was: TQ1, detection: judgment, scopes: [diff, unit], applies-to: 'tests/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
  - {id: TQ2, name: no-failure-story, was: TQ2, detection: judgment, scopes: [diff, unit], applies-to: 'tests/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
  - {id: TQ3, name: duplicated-fake, detection: judgment, scopes: [diff, unit], applies-to: 'tests/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
  - {id: TQ4, name: private-seam-patch, detection: mechanical, scopes: [diff, unit], applies-to: 'tests/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**', guard: pending}
  - {id: TQ5, name: giant-test-file, detection: mechanical, scopes: [diff, unit], applies-to: 'tests/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**', guard: pending}
  - {id: TQ6, name: untested-behavior, was: TQ6, detection: judgment, scopes: [diff], applies-to: 'src/agent_manager/**/*.py, !docs/superpowers/**, !uv.lock, !.claude/worktrees/**'}
---

Principle: tests prove behavior and fail for a reason.

The tier rules are `CLAUDE.md`'s "Test tiers" section: six tiers (unit, `git`,
`brd`, `e2e_fake`, `soak`, `e2e`), each defined by what a test spawns or
touches, with per-test and per-tier budgets, and a placement rule ("a test's
tier is chosen by what it actually spawns or touches, not by the directory it
lives in"). The substitution seams are `docs/standards/architecture.md §5`
row 5.14. `tests/conftest.py` already enforces part of this mechanically (the
unit-tier `PATH` stubs, the budgets, the e2e cap and its `justification:`
lines, the `brd`/`fake_board` mismatch guard); this audit does not re-report
what those guards fail on.

Never "fix" a finding by editing `src/` to suit a test, or a test to suit buggy
code. If the faithful assertion would fail against the current code, report a
deferred code bug instead.

## Checks

### TQ1 wrong-tier

**Level:** principle. **Detection:** judgment (mechanical pre-filter: `grep -nE "subprocess|run_git|Popen|pytest\.mark\.(git|brd|e2e_fake|soak|e2e)" <file>` enumerates candidates; what a test actually spawns needs a read of its fixtures).

A test whose marker does not match what it spawns either runs a real process in
the unit tier (it fails on the stub, or worse, a stub is bypassed through an
absolute path) or pays an opt-in tier's cost for a pure computation. Both make
the default suite slower or flakier and teach the next author the wrong tier.

**Detect:** for each test under audit, follow its fixtures and helpers to what
it spawns: nothing (unit), real `git` in `tmp_path` (`git`), the real `brd`
binary (`brd`), the fake `claude` through production wiring (`e2e_fake`), real
`claude` (`e2e`). Compare with its effective marker, including the directory
auto-mark that `tests/conftest.py` applies to `tests/steps/` (`git`) and
`tests/e2e/` (`e2e_fake`). Flag a mismatch either way, and a test relying on the
directory auto-mark when what it spawns says otherwise.

**Severity:** major for a test that spawns a process its tier forbids, or one
in an opt-in tier that spawns nothing (its coverage silently leaves the
default run); moderate for a test one tier too expensive; minor for an
explicit marker that only repeats the auto-mark.

**Carve-outs:** a test marked `git` because it runs a nested pytest in a
subprocess (`tests/test_tier_guards.py`) is placed by its spawn. Failures the
`tests/conftest.py` guards already raise (the e2e cap, a missing
`justification:` line, a budget overrun) are not reported here.

### TQ2 no-failure-story

**Level:** principle. **Detection:** judgment.

A test earns its place only if a plausible production change makes it fail and
makes no other test fail. A test that cannot name one costs maintenance and
buys false confidence.

**Detect:** for each test under audit, state the production change that would
turn it red. Flag when none exists or the same change also fails sibling
tests: tautologies (a constant equals its own literal, an attribute exists or
is absent, a dataclass field round-trips, a fake returns what the test set
up); mock-only assertions standing in for an outcome (`assert_called`,
`call_count`, `MagicMock` collaborators as the subject —
`grep -nE "assert_called|call_count|MagicMock" <file>`); assertion-free
construction; a bare `pytest.raises(Exception)`; an assertion loose enough to
pass on the wrong output (a substring of a JSON envelope, an `or` between two
outcomes).

**Severity:** major for a mock-only or tautological test; moderate for a vague
`pytest.raises(Exception)` or a loose assertion on an otherwise real test;
minor for one redundant extra assertion.

**Carve-outs:** a fake that records calls where the call's payload is the
contract (the argv `FakeLauncher` receives, the board writes a fake
`board_api` records, the comment body posted) is asserting an outcome, not a
mock interaction. A test pinning a frozen contract value
(`docs/standards/architecture.md §10`: an exit code, an envelope key, the watch
hello schema) has a failure story: the contract changing.

### TQ3 duplicated-fake

**Level:** anti-pattern. **Detection:** judgment (mechanical pre-filter: `grep -rn "^class Fake\|^def fake_" tests/`).

The same fake — the same collaborator, playing the same role — defined in
several test files drifts: one copy learns a new behavior of the real
collaborator and the others keep passing against an outdated double.

**Detect:** for each fake class or factory under audit, grep `tests/` for
another fake of the same collaborator (same name, or the same Protocol, or the
same production function it replaces). Compare what each copy does. Flag two or
more copies that play the same role with differences a shared fake could
absorb as a parameter.

**Severity:** moderate for a third copy or a copy that has already diverged in
behavior the tests rely on; minor for a second identical copy.

**Carve-outs:** fakes playing genuinely different roles under the same name (an
in-process launcher double versus an environment switch for the fake `claude`
binary) are separate fakes; say so and leave them. A fake that only exists to
make one assertion inside one test is local by nature.

### TQ4 private-seam-patch

**Level:** anti-pattern. **Detection:** mechanical (guard: pending; a `monkeypatch.setattr` whose target is an `agent_manager` module attribute that `docs/standards/architecture.md §5` row 5.14 lists as injected, or whose name starts with `_`).

Monkeypatching a module attribute — a re-exported alias such as
`cli.run_direct`, a private helper, a module-level clock — instead of passing
the collaborator through its seam couples the test to how the module looks the
name up. The seam table in `docs/standards/architecture.md §5` (row 5.14) says
how each collaborator gets in: a parameter, a `RunnerFactory`, `Collaborators`,
`driver=`, a `now`.

**Detect:** `grep -nE "monkeypatch\.setattr\((agent_manager|[a-z_]+)[.,]" <file>` and
`grep -nE "monkeypatch\.setattr\([^)]*\"_" <file>`; for each hit, name the seam
from row 5.14 the test could have used instead. Flag a patch of a collaborator
that has an injection seam, and any patch of a `_name`.

**Severity:** moderate for a new patch of a collaborator that has a seam; minor
for a patch of a private helper with no seam yet (the fix is to add one).

**Carve-outs:** the six `cli.run_direct` sites listed in
`docs/standards/architecture.md §11.4` are known exceptions.
`monkeypatch.setenv` and `monkeypatch.chdir` set process state, not a seam. A
patch inside an `e2e_fake` test that must reach the real launcher in a child
process may have no injection path; say so instead of flagging.

### TQ5 giant-test-file

**Level:** anti-pattern. **Detection:** mechanical (guard: pending; `wc -l` over `tests/**/*.py` against the threshold below).

Tests mirror `src/` (`CLAUDE.md`, Conventions). A test file far larger than the
module it mirrors is a sign that several modules' tests, or several tiers,
share one file; it is slow to navigate, collects slowly, and resists the
module split `docs/standards/architecture.md §6` plans, because the tests do
not split along the same lines.

**Detect:** `wc -l tests/**/*.py | sort -n`. Flag a file over 3000 lines. For
each, name the `docs/standards/architecture.md §6` target modules its tests
would follow into and the tiers it mixes (`grep -c "pytest.mark.git" <file>`).

**Severity:** moderate for a file over 6000 lines; minor for 3000-6000.

**Carve-outs:** none by size alone. On a diff scope, report only when the diff
adds tests to a file already over the threshold, and propose where the new
tests should live instead.

### TQ6 untested-behavior

**Level:** principle. **Detection:** judgment.

New production behavior with no test at its required tier is the one finding
that costs nothing now and everything at the next refactor.

**Detect:** for each `src/agent_manager/**` hunk that adds or changes behavior
(a new gate, step, use case, CLI command or option, envelope key, journal
event, status, error class), find the matching test delta in the diff under
`tests/` (mirroring the module's path), and check its tier against what the
behavior needs to be observed: a pure function or a path driven through an
injected fake is unit; git behavior is `git`; the `brd` adapter contract is
`brd`; production wiring is `e2e_fake`. Flag behavior with no test, and
behavior tested only at a more expensive tier than it needs.

**Severity:** major for a new envelope key, exit code, journal event or error
class with no test (those are frozen contracts,
`docs/standards/architecture.md §10`); moderate for a new branch or gate;
minor for behavior already covered by an unchanged test that demonstrably
exercises it (cite it).

**Carve-outs:** pure refactors, renames, moves, docstring and comment changes,
and deletions with no behavior change need no test. A defensive branch
unreachable through any real caller is not a gap. If the required test lives
in a follow-up commit outside the audited range, say so instead of asserting a
violation.

## Reporting

Report findings as `<ID> <file>:<line> — <what> → <fix recipe>`, most severe
first, citing the `CLAUDE.md` section or `docs/standards/architecture.md §N`
rule that makes it a violation. For TQ2, state the failure story you looked
for and could not find. For TQ6, name the production file:line lacking
coverage and the tier the test belongs in. If the satisfying test could live
outside the examined scope, say so instead of asserting a violation.
