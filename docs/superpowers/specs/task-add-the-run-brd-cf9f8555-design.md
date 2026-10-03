# Add the `run_brd` injection seam to `board.py` (card cf9f8555)

Parent story: 13c63fea "Give board.py an injection seam and a FakeBoard fake". Source decision: `docs/superpowers/specs/2026-10-02-test-tier-design.md`, V3 (lines 118-127). This subtask delivers only the first sentence of V3: the seam and the repointed call sites.

## Scope

In `src/agent_manager/board.py`:

1. Add a module-level seam, defined after `_run` (board.py:137-165), mirroring `GitRunner`/`run_git` in `steps/worktree.py:36,57` and `CommandRunner`/`run_command` in `steps/verify.py:95,118`:
   `run_brd: Callable[[Sequence[str], Path | None, str | None], subprocess.CompletedProcess[str]] = _run`
   V3 writes the second parameter as `Path`. Every public function accepts and forwards `repo_dir: Path | None = None`, so the annotation uses `Path | None` to describe what is actually passed. That is the only change to V3's wording.
2. So that `_run` matches that positional three-argument shape, make `input` positional-or-keyword by dropping the bare `*` from `_run`'s signature. The default stays `None` and the body does not change. The existing keyword caller at `tests/test_board.py:1165` (`board._run(["cat"], None, input=text)`) and the two-argument callers at lines 81, 96, 105 and 116 keep working unmodified.
3. Repoint all six public functions to call `run_brd` instead of `_run`: `show`, `tree`, `roots`, `set_status`, `comment_add` and `comment_list` (board.py:231-370). Pass all three arguments positionally: `run_brd(argv, repo_dir, None)` for reads and for `set_status`, and `run_brd(argv, repo_dir, body)` for `comment_add`. This way a replacement only has to honour the declared positional shape, not `_run`'s parameter names.
4. Each call site must look up `run_brd` as a module global at call time. Do not capture it as a default argument, a closure or a local alias. Otherwise `monkeypatch.setattr(board, "run_brd", fake)` from sibling 19b53ab3 would have no effect.

## Invariants (must not change)

- Public function signatures, return types and envelope handling stay the same. `_decode` (board.py:167) and `_validated` (board.py:219) are untouched, and every payload is still validated through `models.Card`, `models.CardNode` or the comment checks at the process boundary (CLAUDE.md convention).
- Locking stays exactly as it is. In `set_status`, the `run_brd(...)` call stays inside the existing `with write_lock(repo_dir):` block, in the same place `_run` occupies today. `comment_add` still takes no lock. The module docstring (board.py:1-28) and the `WRITE_LOCK` / `write_lock()` structure are unchanged.
- `_run` keeps its behaviour: it raises `BoardError` on `FileNotFoundError` and on a non-zero exit with empty stdout, and it reads `BRD` at argv-build time, so `monkeypatch.setattr(board, "BRD", ...)` at `tests/test_board.py:395` still works.
- The `*_argv` builders are unchanged.

## Observable behaviour and error paths

There is no observable change. With the default binding (`run_brd is _run`), every public function spawns the same argv in the same cwd with the same stdin, and it raises the same `BoardError` (same message, `argv`, `exit_code` and `error_type`) on a missing binary, a bare non-zero exit, non-JSON output, a non-envelope, `ok: false`, an ok envelope with a non-zero exit, missing `data`, a payload of the wrong shape, or a validation failure. A replaced `run_brd` gets its output fed through the same `_decode` → shape check → `_validated` path, so a fake that returns a malformed envelope fails the same way real brd would.

## Out of scope

- `FakeBoard`, any `tests/conftest.py` change, and the brd-tier test that pins `FakeBoard` against real brd. Sibling 19b53ab3 owns all of these and consumes this seam.
- Tier markers, `pyproject.toml` changes and the conftest hooks (V1/V2, other stories).
- pytest-xdist as the verify command; the pygents engine, checkpoint format, harness adapter contract and `dispatch.py`'s `LauncherFn`; the e2e tier's 5 tests; milestone 14's `am run --board`.

## Tests

No new tests and no test edits; the subtask description says so explicitly. The existing suite in `tests/test_board.py` (with the `_run` calls at lines 81, 96, 105, 116 and 1165, and the `BRD` monkeypatch at line 395) and every other caller of the board functions must pass unmodified. Under the placement rule (V1, lines 84-92), these existing tests stay where they are. They execute the real `brd` binary or a PATH-shimmed script, which makes them `brd`/`git`-tier by what they execute. Re-tiering them is not this subtask's job. The first tests that drive public functions through a replaced `run_brd` belong to 19b53ab3. Those that use `FakeBoard` are `unit` tier (injected fake, no subprocess). The `FakeBoard`-vs-real-brd pinning test is `brd` tier (opt-in, `-m brd`).

## Verification

`uv run pytest` is green. There is no lint or typecheck command (CLAUDE.md).
