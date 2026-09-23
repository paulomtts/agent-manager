# Subtask 47bd4ee6 — Role bundle loader and the six bundles

Narrows the milestone design (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`, D6 line 71, §4 lines 129-137, §8 lines 320-334) to one module plus the data it loads. Sibling cards own `harness/` — this card produces only what the Claude adapter will later consume.

## Scope

Deliver `src/agent_manager/roles/loader.py` and six role bundles under `src/agent_manager/roles/bundles/<role>/` for `explorer`, `spec_author`, `planner`, `critic`, `coder`, `reviewer`.

Each bundle directory contains:

- `system.md` — the role's standing instructions, non-empty.
- `policy.toml` — allowed tools, default model per harness, default `max_attempts`, required capabilities.
- `methodology/*.md` — vendored methodology text. At minimum the writing-plans format and the TDD discipline exist as vendored files; a bundle references only the methodology its role actually needs (planner/spec_author need writing-plans, coder needs TDD). Do not vendor an adversarial-review checklist unless a role's `system.md` actually references it.
- `VENDORED.lock` — per vendored methodology file: its upstream path and a content hash of the upstream source.

The loader reads a role name, resolves its bundle directory inside the installed package, parses and validates the three file kinds with Pydantic models (everything here crosses a process boundary — it is parsed off disk), and returns one in-memory bundle object carrying: role name, system prompt text, the policy fields, and the methodology documents keyed by filename with their text already read. No runtime plugin or skill resolution, no network, no filesystem writes, no subprocesses (D6). The loader does not decide routing, does not consult a capability matrix, and does not build prompts or argv — it exposes `required_capabilities` and the per-harness model defaults as data for the engine and adapters to use later.

Out of scope: milestone orchestration (census, levels, parallel stories, integrate), non-Claude harnesses, `harness/base.py`, `harness/launcher.py`, `harness/claude.py`, `build_command`, capability-matrix routing logic, and any CLI surface — the spec describes no CLI for `loader.py`.

## Observable behavior

- Loading a known role returns a validated bundle whose `system` text is the exact bytes of `system.md`, whose policy fields come from `policy.toml`, and whose methodology map contains one entry per `methodology/*.md` file with its exact text. Two loads of the same role produce equal bundles; nothing is mutated on disk.
- Listing available roles returns exactly the six role names, sorted, discovered from the bundles directory rather than hard-coded in a way that can drift from what ships.
- A bundle's declared vendored files and the files actually present under `methodology/` agree: every entry in `VENDORED.lock` names a file that exists, and every `methodology/*.md` file has a lock entry.
- The content hash of each vendored methodology file, recomputed at load/verification time, equals the hash recorded in `VENDORED.lock` for that file. Divergence is an explicit, reported condition — never silently tolerated — so a superpowers update becomes a re-sync decision (§8 lines 330-334).
- Every shipped bundle loads cleanly: the six bundles are themselves part of the contract, not just fixtures.

## Error paths

Every failure is a single, typed loader error carrying the role name, the offending path, and a human-readable reason. Failures are never partial: a bundle either loads whole or raises.

- Unknown role name (no such bundle directory).
- Missing `system.md`, missing `policy.toml`, or missing `VENDORED.lock`.
- Empty or whitespace-only `system.md`.
- Malformed TOML in `policy.toml` (surface the parse error with the path).
- `policy.toml` failing schema validation: missing required key, wrong type, unknown/extra key, non-positive `max_attempts`, empty allowed-tools list, or a model default under an unknown-shaped harness key.
- Malformed or schema-invalid `VENDORED.lock`.
- A `VENDORED.lock` entry pointing at a methodology file that does not exist.
- A `methodology/*.md` file present with no `VENDORED.lock` entry.
- A recorded hash that does not match the vendored file's recomputed hash.
- Path escape: a lock entry or methodology reference that resolves outside the bundle directory is rejected rather than followed.

## Test list

Placement rule: §14 of the design spec (lines 477-492) is this repo's test-placement doc. `roles/loader.py` is deterministic parse-and-validate over files that ship with the package — no git repo, no `brd` board, no harness, no network. That is the **pure-functions unit tier** (closest analog: `workflow/loader.py`, §4 line 123), *not* the Steps tier (temp git repos + temp board) and *not* the Adapters tier (reserved for `harness/*.py` `build_command`, a sibling card's scope). All tests below live in the unit tier, in `tests/roles/test_loader.py`, mirroring `src/agent_manager/roles/` per CLAUDE.md.

1. Loading each of the six roles returns a bundle with non-empty system text — parametrized over the six names. *(unit tier)*
2. Listing roles returns exactly the six expected names. *(unit tier)*
3. A loaded bundle's system text equals the on-disk `system.md` bytes verbatim. *(unit tier)*
4. A loaded bundle exposes the policy fields from `policy.toml`: allowed tools, per-harness default model, `max_attempts`, required capabilities. *(unit tier)*
5. Methodology documents are keyed by filename and carry the file's exact text; a role that needs writing-plans and a role that needs TDD each see theirs. *(unit tier)*
6. **Hash-drift test:** for every shipped bundle, the recomputed hash of each vendored methodology file equals the hash recorded in `VENDORED.lock`. This is the test §8 requires; it is a hash comparison over files already on disk, so it stays in the unit tier. *(unit tier)*
7. Lock/filesystem correspondence for every shipped bundle: no lock entry without a file, no methodology file without a lock entry. *(unit tier)*
8. Unknown role name raises the loader error naming the role. *(unit tier)*
9. Missing `system.md` / missing `policy.toml` / missing `VENDORED.lock` each raise, using a synthetic bundle directory built in `tmp_path`. *(unit tier)*
10. Empty `system.md` and whitespace-only `system.md` each raise. *(unit tier)*
11. Malformed TOML raises with the path in the message. *(unit tier)*
12. Policy schema violations raise — missing key, wrong type, non-positive `max_attempts`, extra key, empty allowed-tools list, a model default under an unknown-shaped harness key — parametrized, against synthetic bundles in `tmp_path`. *(unit tier)*
13. Malformed or schema-invalid `VENDORED.lock` raises — bad syntax and a structurally invalid entry (missing key, wrong type) — parametrized, against synthetic bundles in `tmp_path`. *(unit tier)*
14. A tampered vendored file in a synthetic bundle (text edited so its hash no longer matches the lock) raises the drift error naming the file. *(unit tier)*
15. A lock entry whose path resolves outside the bundle directory is rejected. *(unit tier)*

No Steps-tier, Adapters-tier, Engine-tier or end-to-end tests are added by this card.

## Verification

```bash
uv run pytest
```

No separate typecheck or lint command.
