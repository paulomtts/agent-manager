# Card 9a2524a8 — Add the Claude adapter

Subtask of story b4a96f6d ("Roles, the harness adapter protocol and the Claude adapter"), milestone 352e955b. Narrows the agreed design in `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§8 lines 304-318, §14 lines 478-492, decisions D4/D6/D7 at lines 69-72) to one module. It invents no new design.

## Scope

**Owns exactly one module:** `src/agent_manager/harness/claude.py`, plus its unit tests at `tests/harness/test_claude.py`.

The module provides `ClaudeAdapter`: the first concrete implementation of the `HarnessAdapter` Protocol printed at §8 lines 306-313 and coded in `src/agent_manager/harness/base.py:75-98`. It satisfies the Protocol structurally — it inherits from nothing, since `HarnessAdapter` is a pure structural Protocol and is deliberately not `runtime_checkable`.

**Out of scope, and must not be touched or duplicated:**

- `harness/base.py` and `harness/launcher.py` — sibling card 55e503e0, done, and this card's `blocked_by`. Import `Usage` and the `Dispatch` shape; never re-declare them, never widen them.
- Role bundle loading and validation (`roles/loader.py`, `roles/bundles/*`) — sibling card 47bd4ee6, done. This card consumes their output only: an already-materialized prompt file on disk and, where a caller passes it, a model string that came from `policy.toml`'s `default_model["claude"]`. D6 forbids resolving any plugin or skill at run time, and this module resolves nothing.
- Launching. No `subprocess`, no `os.exec*`, no process, no timers in `claude.py`. Execution is `harness/launcher.py::run_direct`, injected by the engine (§14 line 485).
- Reading `result.json`. D4 puts that outside the worktree, in the engine, at §6 step 5. The adapter never opens it and never opens the prompt file either.
- Other harnesses (`codex.py`, `pi.py`), milestone orchestration, the `bwrap`/`container` launcher seams.

## Observable behaviour

### `name` and `capabilities`

`name == "claude"` — the same string a `Dispatch.harness` carries and the same key `Policy.default_model` is indexed by (`roles/loader.py:61-85`, `roles/bundles/coder/policy.toml` → `default_model.claude = "sonnet"`), so routing, policy lookup and journalling all agree on one spelling.

`capabilities` is a `frozenset[str]` of what the harness can genuinely do, for the engine's plan-time capability check (§8 lines 336-339). It is data only; methodology is never a capability — D6 makes methodology vendored prompt text, not a harness feature.

### `build_command(d: Dispatch) -> list[str]`

Pure: same `Dispatch` in, same argv out, no I/O, no clock, no environment read, no process. Returns an argv **list** of `str` — never a shell string, never nested lists, never `Path` objects (§5 line 252 is explicit that `shell_quote` does not port; the launcher hands the list straight to `subprocess` without a shell).

The argv encodes, and only encodes:

- The `claude` executable in non-interactive print mode (`-p`), because an attempt is a one-shot process under D1's stateless dispatch.
- `d.model`, passed through verbatim — the adapter picks no default and rewrites no model name. Defaulting is the role policy's job upstream.
- The materialized role prompt. `Dispatch.prompt_path` is a **path**, not inline text (`models.py:53-70`), and §7 lines 296-298 is explicit that a prompt references an on-disk artifact by absolute path rather than inlining it. So the prompt argument the adapter emits directs the harness to open `d.prompt_path`; the adapter does not read, stat or inline the file's contents, which is what keeps `build_command` pure and its unit tests free of fixture files.
- Permissions bypassed, per D7: v1 launches full-auto.

The argv carries **no cwd flag**: D7's "cwd pinned to the subtask worktree" is realised by the launcher being called with `cwd=d.cwd` (`LauncherFn.__call__`, `launcher.py:56-63`), and duplicating it as a flag would create a second source of truth that can disagree with the one the process actually starts in. The one path that does appear in the argv — `d.prompt_path` — appears absolute, so the command means the same thing regardless of where it is started from. `d.result_path` is never an argv element: D4 passes it to the harness inside the rendered prompt text upstream (§6 step 2-3), not as a command-line flag, so `build_command` neither reads nor emits it; the adapter only checks it is absolute (see Error paths) as a defensive sanity check on the `Dispatch` it was handed.

`d.timeout` is not an argv element either; it is the launcher's kill deadline.

### `parse_usage(stdout: str) -> Usage | None`

Reads the harness's own log text and returns what it cost. It **never raises**, for any input — empty string, megabytes of unrelated chatter, truncated mid-line, or numbers that are negative, non-numeric, infinite or NaN. D4 makes stdout a log and not a channel: an attempt that produced a valid result file but a usage-free log is a *successful* attempt, and a `parse_usage` that raised would turn a cosmetic log change into a failed run.

- No recognisable usage anywhere in the log → `None`.
- Some fields recognised → a `Usage` carrying exactly those, the rest left `None`. Partial reporting is expected and is not an error.
- A recognised field whose value cannot be a valid `Usage` field (negative, `inf`, `nan`, unparseable) is dropped rather than propagated; if dropping leaves nothing, the result is `None`.
- When a log reports usage more than once, the last report wins — it is the cumulative one at the end of the run.

Field names are `tokens_in`, `tokens_out`, `cost` and nothing else. `Usage` is frozen with `extra="forbid"` (`base.py:31-50`) and its names match `Attempt.tokens_in/tokens_out/cost` (`models.py:73-83`) one for one, so the engine's journalling stays a copy and never becomes a translation. `cost` is USD as a float.

## Error paths

`build_command` raises `ValueError` — not a bespoke exception class — in exactly the cases where no retry can help and a silently-wrong command would be worse than a loud stop:

- `d.harness != "claude"`: a dispatch routed to the wrong adapter. Building a Claude argv for a Codex dispatch would run the wrong program against a real worktree.
- `d.cwd`, `d.prompt_path` or `d.result_path` is not absolute: the harness starts in the worktree, and a relative result path would land the result file *inside* the worktree, breaking D4's "outside the worktree" guarantee and getting it committed.

Everything else `Dispatch` could get wrong is already refused by its own validators (`min_length=1` on `harness`/`model`/`role`, `timeout > 0`), so the adapter re-checks none of it.

`parse_usage` has no error path by construction — it returns `None` where another module would raise.

## Test list

All tests below are **unit tier**, per §14 line 484: "Adapters — `build_command` is pure and asserted per harness; the launcher is injected, so no harness is executed in unit tests." No test in this card spawns a process, invokes a real `claude` binary, touches a git worktree, writes a result file, or reaches the engine. Launcher behaviour is card 55e503e0's (`tests/harness/test_launcher.py`); canned-result-file scenarios are the engine card's (§14 line 486); a real harness run is the single opt-in end-to-end test (§14 lines 489-490). Tests live in `tests/harness/test_claude.py`, mirroring `src/agent_manager/harness/claude.py` per CLAUDE.md, and carry the tier-naming docstring pattern of `tests/harness/test_base.py:1-11`. `build_command` is asserted on its returned argv directly; `parse_usage` is asserted against literal stdout strings held in the test.

1. The adapter satisfies the Protocol structurally — every member of `HarnessAdapter.__protocol_attrs__` is present, and it inherits from no base class. (unit)
2. `name == "claude"` and it is the key `roles/bundles/coder/policy.toml`'s `default_model` uses, so policy lookup and dispatch routing cannot drift apart. (unit)
3. `capabilities` is a `frozenset[str]`. (unit)
4. `build_command` returns the full expected argv for a representative `Dispatch` — exact list equality, so the print-mode flag, the model, the prompt-path reference and the permission bypass are each pinned. (unit)
5. Every element of the returned argv is a `str`, and the return is a `list` — §5 line 252's no-shell-string rule. (unit)
6. `d.model` is passed through verbatim, including a model name the adapter has never heard of. (unit)
7. The argv references `d.prompt_path` as an absolute path and does **not** contain the prompt file's contents — asserted with a prompt file that exists on disk with known contents, proving the adapter did not read it. (unit)
8. The argv carries no cwd/working-directory flag and no timeout flag: both belong to the launcher call. (unit)
9. `build_command` is pure — two calls with the same `Dispatch` return equal argvs, and the `Dispatch` is unmutated afterwards. (unit)
10. `build_command` raises `ValueError` when `d.harness` is another harness. (unit)
11. `build_command` raises `ValueError` for a relative `cwd`, a relative `prompt_path` and a relative `result_path` (one case each). (unit)
12. `parse_usage` returns a fully populated `Usage` from a realistic Claude log containing input tokens, output tokens and a cost. (unit)
13. `parse_usage` returns `None` for a log with no usage at all, and for the empty string. (unit)
14. `parse_usage` returns a partial `Usage` when the log reports tokens but no cost. (unit)
15. `parse_usage` never raises on hostile input: truncated mid-line, a negative token count, a non-numeric count, and `inf`/`nan` costs — each returns `None` or a `Usage` with that field dropped, and no exception escapes. (unit)
16. `parse_usage` takes the last usage report when a log contains several. (unit)
17. `parse_usage`'s result validates as a `Usage` with `extra="forbid"` — the adapter produces no key outside `tokens_in`/`tokens_out`/`cost`. (unit)
18. `claude.py` imports no `subprocess`/`os.exec*` and calls nothing in `harness.launcher` — asserted on the module source/imports, pinning "the adapter never launches anything itself". (unit)
