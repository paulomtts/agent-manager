<!-- task-pipeline: validated -->
# Let the agent runner adopt a recorded attempt (card 4aed8212)

Parent: fd673816 "Adoption: a resumed agent phase reuses its recorded result" (milestone 4e0d2a6c, exactly-once). Source: plan `docs/superpowers/plans/2026-09-27-exactly-once.md`, Task 2.1. Branch prefix `m11`, based on master (milestones 9 and 10 merged). Nothing is pushed and the base branch does not move.

## Scope

Files: `src/agent_manager/dispatch.py` and `src/agent_manager/store.py` (only `Store.replay_journal` and `Journal.read`'s new keyword). Tests: `tests/test_dispatch.py` and `tests/test_store.py`.

Out of scope: `src/agent_manager/runtime/compile.py`, `deps.take_adoption`, the `bridge.call_step(adopt, ...)` wiring, and `tests/runtime/test_exactly_once.py`. These belong to sibling 6ecbe6e2, together with every crash-window (W0/W1/W2), Goto-loop, cross-run relaunch, parked-subtask and checkpoint-floor integration test. Steps are not adopted and stay at-least-once. No table gains a column, no CHECK constraint changes, and `checkpoint_floors` is not touched. The CLI envelope and exit codes do not change. `dispatch.py` must not import pygents.

## Deliverables

1. **`dispatch.read_result(result_path: Path | None, model: type[BaseModel] | None) -> Verdict`.** This is the file half of `classify` (dispatch.py:202-259), moved verbatim:
   - `model is None` returns `Verdict("ok", result=None)`.
   - A missing file returns `harness_error`.
   - A UTF-8, JSON or schema failure returns `schema_invalid`.
   - Otherwise it returns `ok` with the JSON-mode dump.

   `classify` keeps its exit-status and timeout checks (lines 227-233) and then delegates to `read_result`. Every existing `classify` test passes unchanged.
2. **`Adopted`.** A plain frozen dataclass: `@dataclass(frozen=True) class Adopted: result: Any; attempt: int; source_run: str`.
3. **`AgentRunner._result_model(phase)`.** The result-model resolution, factored out of `__call__` (lines 428-435). Both `__call__` and `adopt` use it.
4. **`AgentRunner.adopt(self, phase: AgentPhase, context: Mapping[str, Any], *, source_run: str, floor: int) -> Adopted | None`.** It takes and returns plain values and runs these steps in order:
   1. `model = self._result_model(phase)`.
   2. `run = self.store.replay_journal(source_run)`. If this raises `JournalError` (any subclass) or `pydantic.ValidationError`, decline.
   3. Find the subtask whose `card_id == self.card_id` among any story in `run` (the other run's story structure is not assumed to match this run's `self.story_id`), then that subtask's phase named `phase.name`. Candidates are its attempts with `n > floor` and `status == "ok"`. If the subtask or phase is not found in `run`, or there are no candidates, return `None` and add **no** warning.
   4. For the candidate with the highest `n`, call `read_result(None if model is None else attempt.result_path, model)`. If the verdict is not `ok`, decline. Then call `evaluate_gates(phase, gate_values(context, phase.name, verdict.result), self.warnings)`. If it returns a verdict (failed or fatal), decline.
   5. Call `self._record_phase(phase, "done", found.started_at or self.clock(), self.clock(), None)`, where `found` is the matched phase run. Append the warning `phase {name!r} was not dispatched again: attempt {n} of run {source_run} had already succeeded (result reused)`. Return `Adopted(verdict.result, n, source_run)`.

   To decline, append `phase {name!r}: attempt {n} of run {source_run} was not reused ({why}); dispatching again` to `self.warnings` and return `None`. A decline writes no row. `adopt` never copies an attempt row into the current run. It reads attempts only from the journal, never from the `attempts` projection table.
5. **`Store.replay_journal(run_id: str) -> models.Run`.** For its own run it reads `self._journal`. For any other run it reads `Journal(run_id)` with `ignore_torn_tail=True`. It passes the lines to the existing `replay` and returns the result. The whole call runs under `with self._lock:`. It writes nothing, so it does not need `_fenced()`.
6. **`Journal.read(*, ignore_torn_tail: bool = False)`.** When the flag is set, a last non-blank line that is not valid JSON and has no trailing newline is treated as an append still in flight and skipped. Any other non-JSON line still raises `CorruptJournalError`. A missing journal still raises `MissingJournalError`. The default behavior does not change.

## Error paths (all non-fatal; adoption only ever adds `warnings` lines)

- The source journal is missing, corrupt or fails validation: decline.
- No ok attempt has `n > floor`, including orphaned `started` attempts later marked `harness_error`: return `None` silently.
- The result file is deleted, is not JSON or fails the schema: decline, with the reason in `why`.
- A gate now fails or is fatal: decline.

## Tests

Tier rule: design spec §14, "Testing". `adopt`, `read_result`, `classify` and `replay_journal` are engine code, so they are driven directly with the counting `FakeLauncher` and canned result files. They live in the flat engine test files, not in `tests/runtime/` (pygents wiring, which belongs to the sibling) and not in `tests/e2e/` (the single real-harness test). All of the tests below are engine-tier.

`tests/test_dispatch.py` (these reuse `store`, `worktree`, `FakeLauncher`, `_runner`, `_context`, `_workflow`, and a `_succeed_once` helper):
- `test_adopt_returns_the_recorded_result_without_dispatching`: `adopt` returns `Adopted({...}, 1, RUN_ID)`, the launcher is called once, and a "not dispatched again" warning is added.
- `test_an_attempt_at_or_below_the_floor_is_not_adopted`: with `floor=1`, `adopt` returns `None` and adds no decline warning.
- `test_an_orphaned_attempt_is_never_adopted`: the launcher writes a valid result and then crashes, and `adopt` returns `None`.
- `test_a_result_that_no_longer_validates_is_declined`: parametrized over `delete`, `not_json` and `schema`. Each returns `None` and adds a "was not reused" warning.
- `test_a_gate_that_fails_now_declines`: the gate now returns `{"blocked": True}`, so `adopt` returns `None` and adds a decline warning.
- `test_adopt_reads_the_journal_not_the_projection`: after `DELETE FROM attempts`, adoption still succeeds.
- `test_adopt_reads_another_runs_journal`: a runner whose store is bound to `run-2` adopts from `RUN_ID`. `source_run == RUN_ID`, the phase status in `run-2` is `done`, and no attempt row is copied.
- `test_a_missing_source_journal_declines`: with `source_run="never-ran"`, `adopt` returns `None` and adds a decline warning.
- `test_a_phase_without_a_result_model_adopts_none`: the phase has no model, so `Adopted.result is None`.

`tests/test_store.py`:
- `test_replay_journal_of_another_run_ignores_a_torn_tail`: `{"seq": 9` is appended with no newline to `run-2`'s journal, and the replay succeeds without it. A non-JSON line elsewhere still raises `CorruptJournalError`.
- `test_replay_journal_holds_the_store_lock`: a `record_*` call blocked on the lock in another thread cannot add a line in the middle of the replay.

Existing `classify` tests must stay unchanged and green.

## Verification

`uv run pytest`: the whole suite is green, including `tests/e2e`. There is no lint or typecheck step.

---

# Adopt a Recorded Attempt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `dispatch.AgentRunner` a plain-value `adopt` primitive that reuses a recorded, re-validated, re-gated `ok` attempt from a run's journal instead of dispatching again, plus the `read_result`, `Adopted`, `Store.replay_journal` and `Journal.read(ignore_torn_tail=...)` pieces it needs.

**Architecture:** `classify` is split so its file half (`read_result`) can re-judge an old `result.json` without a launcher `Outcome`. `Store.replay_journal` folds a run's journal (own or another run's) into a `models.Run` under the store lock, tolerating only an unterminated final line on another run's journal. `AgentRunner.adopt` walks that tree for this card's phase, picks the highest `ok` attempt above the floor, re-reads its result file, re-runs the gates, and either records the phase `done` and returns `Adopted`, or appends a decline warning and returns `None`.

**Tech Stack:** Python 3, Pydantic v2, SQLite (stdlib `sqlite3`), pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-let-the-agent-runner-4aed8212-design.md` (prepended above, verbatim).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-let-the-agent-runner-4aed8212`; run every command from there. This branch (`m11/task-let-the-agent-runner-4aed8212`) is cut from `m11/task-compute-the-floor-at-94088f7e`; no other subtask's adoption code is assumed to exist on it.

Line numbers below are this worktree's, which are a few lines off the spec's (the spec quotes the plan's baseline): `classify` is `src/agent_manager/dispatch.py:198-255`, the model resolution inside `__call__` is `dispatch.py:421-431`, `Journal.__init__` is `src/agent_manager/store.py:234-238`, `Journal.read` is `store.py:256-279`, `Store.rebuild_from_journal` ends at `store.py:1557` with `_delete_run` at `store.py:1559`.

## Global Constraints

- `dispatch.py` must not import pygents. `AgentRunner.adopt` takes and returns plain values.
- No table gains a column, no CHECK constraint changes, `checkpoint_floors` is not touched, no CLI change.
- Do not touch `src/agent_manager/runtime/compile.py`, `deps.take_adoption`, `bridge.call_step` wiring, or create `tests/runtime/test_exactly_once.py` (sibling 6ecbe6e2 owns them).
- Never adopt a non-`ok` attempt; never skip re-reading the result file or re-running the gates.
- `adopt` reads attempts only from the journal (`Store.replay_journal`), never from the `attempts` table, and never copies an attempt row into the current run.
- A decline writes no row; only an adoption records the phase, through `_record_phase(phase, "done", ...)`.
- Success warning text, exactly: `phase {name!r} was not dispatched again: attempt {n} of run {source_run} had already succeeded (result reused)`.
- Decline warning text, exactly: `phase {name!r}: attempt {n} of run {source_run} was not reused ({why}); dispatching again`.
- Internal state is a plain `@dataclass(frozen=True)` (`Adopted`, like `Verdict`), not a pydantic model.
- `Journal.read()` with no argument behaves exactly as today.
- Every existing `classify` test passes unchanged.
- Verification: `uv run pytest`, whole suite green including `tests/e2e`. No lint or typecheck step exists.

### Gap-fills the spec does not decide (made here, deliberately)

1. **`Journal._for_reading(run_id)`.** The spec says "for any other run it reads `Journal(run_id)` with `ignore_torn_tail=True`". But `Journal.__init__` (`store.py:234-238`) calls `self.last_seq()`, which calls `self.read()` with the default flag, so constructing `Journal("run-2")` on a journal with a torn tail raises `CorruptJournalError` before `replay_journal` can ever pass the flag, and the spec's own torn-tail test could not pass. Changing `last_seq` to ignore the tail would let the owning process glue its next append onto the torn bytes, so it stays as is. Instead a private classmethod `Journal._for_reading(run_id)` builds a read-only `Journal` without the sequence scan (`_seq = 0`, never appended to). It also does not `mkdir` the run directory, so a decline for a run that never existed (`"never-ran"`) leaves no stray directory behind.
2. **Decline before an attempt is known.** When `replay_journal` fails there is no attempt number yet. The decline keeps the mandated format and renders the number as `?`: `phase 'explore': attempt ? of run never-ran was not reused (MissingJournalError: ...); dispatching again`.
3. **`why`** is `verdict.detail or verdict.status` for a result or gate verdict, and `_render_error(error)` (`"{Type}: {message}"`, the existing helper) prefixed with `its journal cannot be read: ` for a replay failure.
4. **Binding errors still raise.** `evaluate_gates` raises `EngineError` when a gate names a parameter nothing supplies; `adopt` lets it propagate, exactly as `__call__` does, because it is a workflow bug and dispatching again cannot fix it. An unknown result-model name likewise raises from `_result_model` before anything is read.

## Review Focus

1. Several `ok` attempts above the floor (a phase that succeeded, then was re-run): the highest `n` must be the one adopted, never the first. Test: `test_the_highest_ok_attempt_above_the_floor_is_adopted` in Task 3.
2. The source run recorded this card under a different story, or never recorded this card or this phase at all: the first must still adopt, the second must return `None` without any warning. Tests: `test_adopt_finds_the_card_under_any_story_of_the_source_run` and `test_a_phase_or_card_the_source_run_never_recorded_adopts_nothing_silently` in Task 3.
3. A gate that raises (fatal) while the old result is re-judged: `adopt` must decline with a warning, not raise out of the resume. Test: `test_a_gate_that_raises_now_declines_rather_than_raising` in Task 3.
4. A torn final line in the store's *own* journal: only another run's journal may tolerate it; `replay_journal` of the own run must still raise `CorruptJournalError`. Test: `test_replay_journal_of_its_own_run_never_ignores_a_torn_tail` in Task 2.
5. A source run that never existed: the decline must not create a run directory for it as a side effect. Asserted inside `test_a_missing_source_journal_declines` in Task 3.

---

### Task 1: Split `read_result` out of `classify`

**Files:**
- Modify: `src/agent_manager/dispatch.py:198-255` (`classify`)
- Test: `tests/test_dispatch.py` (insert after `test_stdout_is_never_the_channel`, before `def AGENT_DOCUMENT`)

**Interfaces:**
- Consumes: existing `dispatch.Verdict(status, result=None, detail=None, fatal=False)`.
- Produces: `dispatch.read_result(result_path: Path | None, model: type[BaseModel] | None) -> Verdict`. `classify(outcome, result_path, model)` keeps its signature and delegates to it after the timeout and exit-code checks.

- [ ] **Step 1: Write the failing tests**

In `tests/test_dispatch.py`, insert immediately before the line `def AGENT_DOCUMENT(functions: dict[str, object]) -> phases.Workflow:`:

```python
def test_read_result_with_no_model_is_ok_without_touching_the_path():
    assert dispatch.read_result(None, None) == dispatch.Verdict("ok", result=None)


def test_read_result_judges_the_file_alone(tmp_path):
    path = tmp_path / "result.json"

    missing = dispatch.read_result(path, FakeResult)
    assert missing.status == "harness_error"
    assert "wrote no result file" in missing.detail

    path.write_text(NOT_JSON, encoding="utf-8")
    assert dispatch.read_result(path, FakeResult).status == "schema_invalid"

    path.write_text(INVALID_RESULT, encoding="utf-8")
    assert dispatch.read_result(path, FakeResult).status == "schema_invalid"

    path.write_bytes(b"\xff\xfe not utf-8")
    assert dispatch.read_result(path, FakeResult).status == "schema_invalid"

    path.write_text(VALID_RESULT, encoding="utf-8")
    assert dispatch.read_result(path, FakeResult) == dispatch.Verdict(
        "ok", result={"summary": "explored the tree", "ok": True}
    )


def test_classify_after_a_clean_exit_is_read_result(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(VALID_RESULT, encoding="utf-8")

    assert dispatch.classify(_outcome(tmp_path), path, FakeResult) == dispatch.read_result(
        path, FakeResult
    )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -v -k "read_result"`
Expected: FAIL, each with `AttributeError: module 'agent_manager.dispatch' has no attribute 'read_result'`.

- [ ] **Step 3: Split `classify`**

In `src/agent_manager/dispatch.py`, replace the body of `classify` from `    if model is None:` (line 230) through `    return Verdict("ok", result=validated.model_dump(mode="json"))` (line 255) with a single delegation, and add `read_result` right after it. The resulting code from the end of `classify`'s docstring to the start of `gate_values` is:

```python
    if outcome.timed_out:
        return Verdict(
            "harness_error",
            detail=f"the harness timed out and was killed after {outcome.duration:.1f}s",
        )
    if outcome.exit_code != 0:
        return Verdict("harness_error", detail=f"the harness exited {outcome.exit_code}")
    return read_result(result_path, model)


def read_result(result_path: Path | None, model: type[BaseModel] | None) -> Verdict:
    """The file half of `classify`: judge a result file with no launcher report.

    Split out so `AgentRunner.adopt` can re-judge an attempt recorded by an
    earlier process, which left a file on disk but no `Outcome` in memory. The
    order and every message are `classify`'s, unchanged: no model is `ok` with
    no result and the path is never touched, a missing file is a
    `harness_error`, and every way an existing file fails to validate is
    `schema_invalid`. An `ok` result is the JSON-mode dump, for the reason
    `classify`'s docstring gives.
    """
    if model is None:
        return Verdict("ok", result=None)
    if result_path is None or not result_path.is_file():
        return Verdict(
            "harness_error", detail=f"the harness wrote no result file at {result_path}"
        )
    try:
        text = result_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        return Verdict(
            "schema_invalid", detail=f"{result_path} is not valid UTF-8 text: {error}"
        )
    except OSError as error:
        return Verdict(
            "schema_invalid",
            detail=f"{result_path} cannot be read: {type(error).__name__}: {error}",
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return Verdict("schema_invalid", detail=f"{result_path} is not valid JSON: {error}")
    try:
        validated = model.model_validate(data)
    except ValidationError as error:
        return Verdict("schema_invalid", detail=str(error))
    return Verdict("ok", result=validated.model_dump(mode="json"))
```

`classify`'s docstring stays exactly as it is.

- [ ] **Step 4: Run the new and the existing classify tests**

Run: `uv run pytest tests/test_dispatch.py -v -k "read_result or classif or result_less or stdout_is_never"`
Expected: PASS, every one; no existing test edited.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "refactor(dispatch): split read_result out of classify"
```

---

### Task 2: `Journal.read(ignore_torn_tail=...)` and `Store.replay_journal`

**Files:**
- Modify: `src/agent_manager/store.py:234-279` (`Journal.__init__` neighbourhood and `Journal.read`)
- Modify: `src/agent_manager/store.py:1557-1559` (new method between `rebuild_from_journal` and `_delete_run`)
- Test: `tests/test_store.py` (append at the end of the file)

**Interfaces:**
- Consumes: existing `store.replay(lines) -> models.Run`, `store.Journal`, `store.JOURNAL_NAME`, `paths.data_dir()`, `Store._lock` (an `RLock`).
- Produces: `Journal.read(self, *, ignore_torn_tail: bool = False) -> list[JournalLine]`; private `Journal._for_reading(cls, run_id: str) -> Journal`; `Store.replay_journal(self, run_id: str) -> models.Run`. Task 3 calls `self.store.replay_journal(source_run)` and catches `store.JournalError` and `pydantic.ValidationError`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python
# -- replaying a journal for adoption (exactly-once Task 2.1) ----------------

ADOPTING_RUN_ID = "run-2"


def test_ignore_torn_tail_skips_only_an_unterminated_last_line(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "run_id"')

    with pytest.raises(store.CorruptJournalError):
        journal.read()
    assert [line.payload["i"] for line in journal.read(ignore_torn_tail=True)] == [0]


def test_ignore_torn_tail_still_rejects_a_bad_line_before_the_last(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n")
        handle.write('{"seq": 3')

    with pytest.raises(store.CorruptJournalError) as excinfo:
        journal.read(ignore_torn_tail=True)
    assert ":2:" in str(excinfo.value)


def test_ignore_torn_tail_still_raises_for_a_missing_journal(repo):
    journal = store.Journal("run-never-started")
    with pytest.raises(store.MissingJournalError):
        journal.read(ignore_torn_tail=True)


def test_replay_journal_of_another_run_ignores_a_torn_tail(repo):
    other = store.Store.open(repo, ADOPTING_RUN_ID)
    try:
        other.record_run(_run(repo, ADOPTING_RUN_ID))
        other.record_story(_story())
    finally:
        other.close()
    torn = paths.run_dir(ADOPTING_RUN_ID) / store.JOURNAL_NAME
    with torn.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 9')

    st = store.Store.open(repo, RUN_ID)
    try:
        replayed = st.replay_journal(ADOPTING_RUN_ID)

        # The same bytes, now newline-terminated, are a finished line that is
        # not JSON: that is corruption, not an append in flight.
        with torn.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        with pytest.raises(store.CorruptJournalError) as excinfo:
            st.replay_journal(ADOPTING_RUN_ID)
    finally:
        st.close()

    assert replayed.id == ADOPTING_RUN_ID
    assert [story.card_id for story in replayed.stories] == ["8831189b"]
    assert ":3:" in str(excinfo.value)


def test_replay_journal_of_its_own_run_never_ignores_a_torn_tail(repo):
    # Review Focus 4: only another run, which may be live elsewhere, gets the
    # benefit of the doubt. The own journal is this process's to write.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        with st.journal.path.open("a", encoding="utf-8") as handle:
            handle.write('{"seq": 9')
        with pytest.raises(store.CorruptJournalError):
            st.replay_journal(RUN_ID)
    finally:
        st.close()


def test_replay_journal_of_its_own_run_returns_the_recorded_tree(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        replayed = st.replay_journal(RUN_ID)
        loaded = st.load_run(RUN_ID)
    finally:
        st.close()

    assert replayed == loaded


def test_replay_journal_holds_the_store_lock(repo, monkeypatch):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        real_read = st.journal.read
        seen: list[bool] = []
        writers: list[threading.Thread] = []

        def interleaving_read(**kwargs):
            # A record_* started from another thread mid-replay must wait for
            # the replay: if it could land now, this read would include it.
            seen.append(_held_elsewhere(st._lock))
            writer = threading.Thread(
                target=st.record_subtask, args=("8831189b", _subtask())
            )
            writers.append(writer)
            writer.start()
            writer.join(timeout=0.2)
            seen.append(writer.is_alive())
            return real_read(**kwargs)

        monkeypatch.setattr(st.journal, "read", interleaving_read)
        replayed = st.replay_journal(RUN_ID)
        writers[0].join()
        after = store.replay(real_read())
    finally:
        st.close()

    assert seen == [True, True]
    assert replayed.stories[0].subtasks == []
    assert [subtask.card_id for subtask in after.stories[0].subtasks] == ["ef248597"]


def test_replay_journal_writes_nothing(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        before = st.journal.path.read_bytes()
        st.connection.execute("DELETE FROM attempts")
        st.connection.commit()
        st.replay_journal(RUN_ID)
        attempts = st.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        after = st.journal.path.read_bytes()
    finally:
        st.close()

    assert after == before
    assert attempts == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_store.py -v -k "torn_tail or replay_journal"`
Expected: FAIL. The `ignore_torn_tail` tests with `TypeError: Journal.read() got an unexpected keyword argument 'ignore_torn_tail'`; the `replay_journal` tests with `AttributeError: 'Store' object has no attribute 'replay_journal'`.

- [ ] **Step 3: Add the flag to `Journal.read` and a read-only constructor**

In `src/agent_manager/store.py`, insert this classmethod directly after `Journal.__init__` (after line 238, `self._seq = self.last_seq()`):

```python

    @classmethod
    def _for_reading(cls, run_id: str) -> "Journal":
        """Another run's journal, opened only to be read.

        `__init__` scans the file for its highest `seq` with the default
        `read()`, which would raise on the very torn tail
        `read(ignore_torn_tail=True)` exists to tolerate, and `paths.run_dir`
        would create a directory for a run that never existed. This instance
        is never appended to, so it needs neither: `_seq` stays 0.
        """
        journal = cls.__new__(cls)
        journal.run_id = run_id
        journal.path = paths.data_dir() / "runs" / run_id / JOURNAL_NAME
        journal._lock = threading.Lock()
        journal._seq = 0
        return journal
```

Then replace `Journal.read` (lines 256-279) with:

```python
    def read(self, *, ignore_torn_tail: bool = False) -> list[JournalLine]:
        """Every line, validated, in sequence order.

        Blank lines are skipped: a crash between the write and the flush can
        leave one. Anything else that is not JSON is an error naming the line.

        `ignore_torn_tail` is for reading *another* run's journal, which a
        process elsewhere may be appending to right now: a final line that is
        not JSON and has no trailing newline is that append in flight, and is
        skipped. A non-JSON line that is newline-terminated, or that is not the
        last, is still `CorruptJournalError`. Lines are ASCII (`json.dumps`
        escapes), so a cut can never split a character.
        """
        if not self.path.exists():
            raise MissingJournalError(
                f"no journal for run {self.run_id!r} at {self.path}"
            )
        with self.path.open(encoding="utf-8") as handle:
            texts = handle.readlines()
        lines: list[JournalLine] = []
        for number, text in enumerate(texts, start=1):
            if not text.strip():
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as error:
                torn = number == len(texts) and not text.endswith("\n")
                if ignore_torn_tail and torn:
                    continue
                raise CorruptJournalError(
                    f"{self.path}:{number}: line is not JSON: {error}"
                ) from error
            lines.append(JournalLine.model_validate(record))
        lines.sort(key=lambda line: line.seq)
        return lines
```

- [ ] **Step 4: Add `Store.replay_journal`**

In `src/agent_manager/store.py`, insert between the end of `rebuild_from_journal` (`            return run`, line 1557) and `    def _delete_run(self, run_id: str) -> None:`:

```python

    def replay_journal(self, run_id: str) -> models.Run:
        """The §9 tree `run_id`'s journal records, without touching any row.

        Adoption reads attempts here and never from the `attempts` projection.
        The store lock is held across the read: `Journal.read` takes no lock,
        and other lanes of a milestone resume append to this run's journal
        through this store, so an unlocked read could meet half a line. Nothing
        is written, so there is no `_fenced()`. Another run's journal may be
        live in another process, so only there is a torn final line ignored.
        """
        with self._lock:
            if run_id == self.run_id:
                lines = self._journal.read()
            else:
                lines = Journal._for_reading(run_id).read(ignore_torn_tail=True)
            return replay(lines)
```

The own-run call passes no keyword on purpose: `test_rebuild_and_load_run_hold_the_store_lock_on_the_shared_connection` patches `journal.read` with a zero-argument spy, and the own-run path must keep the default behaviour.

- [ ] **Step 5: Run the store tests**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS, the whole file, including `test_a_truncated_final_line_is_an_error_but_a_blank_one_is_not` and `test_opening_a_corrupt_journal_raises_at_open` unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): replay a run's journal under the store lock, tolerating another run's torn tail"
```

---

### Task 3: `Adopted`, `_result_model` and `AgentRunner.adopt`

**Files:**
- Modify: `src/agent_manager/dispatch.py:41` (import), after `Verdict` (`dispatch.py:153-169`), `AgentRunner.__call__` model resolution (`dispatch.py:421-431`), new methods after `_record_attempt` (`dispatch.py:597-598`)
- Test: `tests/test_dispatch.py` (append at the end of the file)

**Interfaces:**
- Consumes: `dispatch.read_result(result_path, model) -> Verdict` (Task 1); `Store.replay_journal(run_id) -> models.Run`, `store.JournalError` (Task 2); existing `evaluate_gates(phase, values, warnings) -> Verdict | None`, `gate_values(context, phase_name, result) -> dict`, `AgentRunner._record_phase(phase, status, started_at, ended_at, detail)`, `_render_error(error) -> str`; `models.Run.stories[].subtasks[].card_id/.phases[].name/.started_at/.attempts[].n/.status/.result_path`.
- Produces: `dispatch.Adopted(result: Any, attempt: int, source_run: str)` (frozen dataclass); `AgentRunner._result_model(self, phase) -> type[BaseModel] | None`; `AgentRunner.adopt(self, phase: phase_model.AgentPhase, context: Mapping[str, Any], *, source_run: str, floor: int) -> Adopted | None`. Sibling 6ecbe6e2 calls `adopt` through `bridge.call_step`; nothing in this task wires it.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_dispatch.py`:

```python
# ── adoption (exactly-once Task 2.1) ─────────────────────────────────────────
# Engine tier: `adopt` is called directly on a runner that already ran the
# phase once through the counting FakeLauncher, so `launcher.calls` proves
# nothing was dispatched again. The pygents wiring is sibling 6ecbe6e2's.

OTHER_RUN_ID = "run-2"
OTHER_VALID_RESULT = json.dumps({"summary": "explored it again", "ok": True})
EXPLORED = {"summary": "explored the tree", "ok": True}


def _reused(n: int, source_run: str = RUN_ID, name: str = "explore") -> str:
    return (
        f"phase {name!r} was not dispatched again: attempt {n} of run {source_run} "
        "had already succeeded (result reused)"
    )


def _declines(runner) -> list[str]:
    return [warning for warning in runner.warnings if "was not reused" in warning]


def _seed(opened, run_id: str = RUN_ID, story_id: str = STORY_ID) -> None:
    """The run, story and subtask lines `replay` needs above any phase line."""
    opened.record_run(
        models.Run(
            id=run_id,
            workflow="agentic",
            repo_dir=Path("/repo"),
            base_branch="master",
            branch_prefix="m11/",
        )
    )
    opened.record_story(models.StoryRun(card_id=story_id, title="Adoption", level=0))
    opened.record_subtask(
        story_id,
        models.SubtaskRun(card_id=CARD, branch=f"m11/task-{CARD}", base_branch="master"),
    )


def _passing_phase() -> phases.AgentPhase:
    return _model_phase(lambda result: None)


def _succeed_once(
    store, tmp_path, worktree, phase=None, results=(VALID_RESULT,), story_id=STORY_ID
):
    """Seed the run tree, then run `phase` once: attempt 1 `ok`, phase `done`."""
    _seed(store, story_id=story_id)
    phase = _passing_phase() if phase is None else phase
    launcher = FakeLauncher(results=list(results))
    runner, _ = _runner(store, launcher, tmp_path, worktree, story_id=story_id)
    runner(phase, _context(worktree), _rendered())
    return runner, launcher, phase


@dataclass
class CrashingLauncher(FakeLauncher):
    """Writes its canned result, then dies before the attempt is judged."""

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        super().__call__(argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path)
        raise RuntimeError("the manager died after the harness wrote its result")


def test_adopt_returns_the_recorded_result_without_dispatching(store, tmp_path, worktree):
    runner, launcher, phase = _succeed_once(store, tmp_path, worktree)

    adopted = runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0)

    assert adopted == dispatch.Adopted(EXPLORED, 1, RUN_ID)
    assert len(launcher.calls) == 1
    assert runner.warnings == [_reused(1)]
    assert _phase_statuses(store) == [
        ("explore", "started"), ("explore", "done"), ("explore", "done")
    ]
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]


def test_adopted_is_a_frozen_plain_value():
    adopted = dispatch.Adopted({"a": 1}, 2, RUN_ID)

    assert (adopted.result, adopted.attempt, adopted.source_run) == ({"a": 1}, 2, RUN_ID)
    with pytest.raises(dataclasses.FrozenInstanceError):
        adopted.attempt = 3


def test_an_attempt_at_or_below_the_floor_is_not_adopted(store, tmp_path, worktree):
    runner, launcher, phase = _succeed_once(store, tmp_path, worktree)
    lines = len(store.journal.read())

    assert runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=1) is None
    assert runner.warnings == []
    assert len(store.journal.read()) == lines
    assert len(launcher.calls) == 1


def test_an_orphaned_attempt_is_never_adopted(store, tmp_path, worktree):
    _seed(store)
    phase = _passing_phase()
    runner, _ = _runner(store, CrashingLauncher(results=[VALID_RESULT]), tmp_path, worktree)
    with pytest.raises(RuntimeError, match="the manager died"):
        runner(phase, _context(worktree), _rendered())
    assert (paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "result.json").is_file()
    assert _attempt_statuses(store) == [(1, "started")]

    assert runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0) is None
    assert runner.warnings == []

    # Resume later marks the orphan `harness_error`; it is still never adopted.
    started = next(
        line.payload for line in store.journal.read() if line.event == "attempt_upsert"
    )
    store.record_attempt(
        STORY_ID,
        CARD,
        "explore",
        models.Attempt.model_validate(started).model_copy(update={"status": "harness_error"}),
    )
    assert runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0) is None
    assert runner.warnings == []


@pytest.mark.parametrize(
    ("damage", "why"),
    [
        ("delete", "the harness wrote no result file at"),
        ("not_json", "is not valid JSON"),
        ("schema", "summary"),
    ],
)
def test_a_result_that_no_longer_validates_is_declined(
    damage, why, store, tmp_path, worktree
):
    runner, launcher, phase = _succeed_once(store, tmp_path, worktree)
    result_file = paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "result.json"
    if damage == "delete":
        result_file.unlink()
    elif damage == "not_json":
        result_file.write_text(NOT_JSON, encoding="utf-8")
    else:
        result_file.write_text(INVALID_RESULT, encoding="utf-8")
    lines = len(store.journal.read())

    assert runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0) is None

    [warning] = _declines(runner)
    assert warning.startswith(f"phase 'explore': attempt 1 of run {RUN_ID} was not reused (")
    assert warning.endswith("); dispatching again")
    assert why in warning
    assert len(store.journal.read()) == lines
    assert len(launcher.calls) == 1


def test_a_gate_that_fails_now_declines(store, tmp_path, worktree):
    runner, _, _ = _succeed_once(store, tmp_path, worktree)
    lines = len(store.journal.read())

    failing = _model_phase(lambda result: {"blocked": True})
    assert runner.adopt(failing, _context(worktree), source_run=RUN_ID, floor=0) is None

    [warning] = _declines(runner)
    assert warning.startswith(f"phase 'explore': attempt 1 of run {RUN_ID} was not reused (")
    assert "gate '<lambda>' failed: blocked=True" in warning
    assert len(store.journal.read()) == lines


def test_a_gate_that_raises_now_declines_rather_than_raising(store, tmp_path, worktree):
    # Review Focus 3: a fatal gate verdict is a decline, never an exception
    # out of the resume.
    runner, _, _ = _succeed_once(store, tmp_path, worktree)

    def output_gate(result):
        raise RuntimeError("broken now")

    adopted = runner.adopt(
        _model_phase(output_gate), _context(worktree), source_run=RUN_ID, floor=0
    )

    assert adopted is None
    [warning] = _declines(runner)
    assert "gate 'output_gate' raised RuntimeError: broken now" in warning


def test_adopt_reads_the_journal_not_the_projection(store, tmp_path, worktree):
    runner, _, phase = _succeed_once(store, tmp_path, worktree)
    store.connection.execute("DELETE FROM attempts")
    store.connection.commit()
    assert store.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0

    adopted = runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0)

    assert adopted == dispatch.Adopted(EXPLORED, 1, RUN_ID)


def test_adopt_reads_another_runs_journal(store, tmp_path, worktree):
    _succeed_once(store, tmp_path, worktree)
    other = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        launcher = FakeLauncher(results=[VALID_RESULT])
        runner, _ = _runner(other, launcher, tmp_path, worktree, run_id=OTHER_RUN_ID)
        adopted = runner.adopt(
            _passing_phase(), _context(worktree), source_run=RUN_ID, floor=0
        )
        phase_lines = _phase_statuses(other)
        attempt_lines = _attempt_statuses(other)
        copied = other.connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE run_id = ?", (OTHER_RUN_ID,)
        ).fetchone()[0]
    finally:
        other.close()

    assert adopted == dispatch.Adopted(EXPLORED, 1, RUN_ID)
    assert adopted.source_run == RUN_ID
    assert launcher.calls == []
    assert phase_lines == [("explore", "done")]
    assert attempt_lines == []
    assert copied == 0
    assert runner.warnings == [_reused(1)]


def test_adopt_finds_the_card_under_any_story_of_the_source_run(store, tmp_path, worktree):
    # Review Focus 2: the source run's story structure is not assumed to match.
    _succeed_once(store, tmp_path, worktree, story_id="5f0c1a2e")
    other = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        runner, _ = _runner(
            other, FakeLauncher(results=[VALID_RESULT]), tmp_path, worktree,
            run_id=OTHER_RUN_ID,
        )
        adopted = runner.adopt(
            _passing_phase(), _context(worktree), source_run=RUN_ID, floor=0
        )
    finally:
        other.close()

    assert adopted == dispatch.Adopted(EXPLORED, 1, RUN_ID)


def test_a_phase_or_card_the_source_run_never_recorded_adopts_nothing_silently(
    store, tmp_path, worktree
):
    # Review Focus 2, the other half: nothing to adopt is not a decline.
    runner, _, phase = _succeed_once(store, tmp_path, worktree)
    lines = len(store.journal.read())

    never_ran = _model_phase(lambda result: None, name="review")
    assert runner.adopt(never_ran, _context(worktree), source_run=RUN_ID, floor=0) is None

    stranger, _ = _runner(
        store, FakeLauncher(results=[VALID_RESULT]), tmp_path, worktree, card_id="0000aaaa"
    )
    assert stranger.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0) is None

    assert runner.warnings == []
    assert stranger.warnings == []
    assert len(store.journal.read()) == lines


def test_the_highest_ok_attempt_above_the_floor_is_adopted(store, tmp_path, worktree):
    # Review Focus 1.
    runner, launcher, phase = _succeed_once(
        store, tmp_path, worktree, results=(VALID_RESULT, OTHER_VALID_RESULT)
    )
    runner(phase, _context(worktree), _rendered())
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok"), (2, "started"), (2, "ok")]
    again = {"summary": "explored it again", "ok": True}

    assert runner.adopt(
        phase, _context(worktree), source_run=RUN_ID, floor=0
    ) == dispatch.Adopted(again, 2, RUN_ID)
    assert runner.adopt(
        phase, _context(worktree), source_run=RUN_ID, floor=1
    ) == dispatch.Adopted(again, 2, RUN_ID)
    assert runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=2) is None
    assert len(launcher.calls) == 2


def test_a_missing_source_journal_declines(store, tmp_path, worktree):
    runner, launcher, phase = _succeed_once(store, tmp_path, worktree)
    lines = len(store.journal.read())

    assert runner.adopt(phase, _context(worktree), source_run="never-ran", floor=0) is None

    [warning] = _declines(runner)
    assert warning.startswith(
        "phase 'explore': attempt ? of run never-ran was not reused ("
        "its journal cannot be read: MissingJournalError: "
    )
    assert warning.endswith("); dispatching again")
    assert len(store.journal.read()) == lines
    assert len(launcher.calls) == 1
    # Review Focus 5: declining leaves no run directory for a run that never was.
    assert not (paths.data_dir() / "runs" / "never-ran").exists()


def test_a_phase_without_a_result_model_adopts_none(store, tmp_path, worktree):
    phase = _model_phase(
        name="spec", result=None, retry=None, writes="docs/superpowers/specs/{stem}.md"
    )
    runner, launcher, _ = _succeed_once(store, tmp_path, worktree, phase=phase, results=(None,))

    adopted = runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0)

    assert adopted == dispatch.Adopted(None, 1, RUN_ID)
    assert adopted.result is None
    assert runner.warnings == [_reused(1, name="spec")]
    assert len(launcher.calls) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -v -k "adopt or orphaned or floor or no_longer_validates or fails_now or raises_now or never_recorded or highest_ok or missing_source"`
Expected: FAIL. `test_adopted_is_a_frozen_plain_value` with `AttributeError: module 'agent_manager.dispatch' has no attribute 'Adopted'`; every other new test with `AttributeError: 'AgentRunner' object has no attribute 'adopt'` (the seeding run through `__call__` succeeds first).

- [ ] **Step 3: Import `JournalError`**

In `src/agent_manager/dispatch.py`, replace line 41:

```python
from agent_manager.store import Store
```

with:

```python
from agent_manager.store import JournalError, Store
```

- [ ] **Step 4: Add `Adopted` after `Verdict`**

In `src/agent_manager/dispatch.py`, insert directly after the `Verdict` class (after `    fatal: bool = False`, line 169):

```python


@dataclass(frozen=True)
class Adopted:
    """An earlier attempt's result, reused instead of dispatching again.

    Internal-only state, so a dataclass (CLAUDE.md). `result` is the
    re-validated JSON-mode dump (or `None` for a result-less phase), `attempt`
    the recorded attempt number it came from, `source_run` the run whose
    journal recorded it.
    """

    result: Any
    attempt: int
    source_run: str


def _recorded_phase(
    run: models.Run, card_id: str, phase_name: str
) -> models.PhaseRun | None:
    """This card's phase `phase_name` in `run`, under whichever story holds it.

    The source run's story structure is not assumed to match the current
    run's, so every story is searched.
    """
    for story in run.stories:
        for subtask in story.subtasks:
            if subtask.card_id != card_id:
                continue
            for recorded in subtask.phases:
                if recorded.name == phase_name:
                    return recorded
    return None
```

- [ ] **Step 5: Factor `_result_model` out of `__call__`**

In `AgentRunner.__call__`, replace (lines 421-431):

```python
        # A declared phase carries its result model as the class itself, which
        # is used as-is; a result given by name is looked up in
        # `result_models`, and a name the table lacks is refused here.
        if phase.result is None:
            model = None
        elif isinstance(phase.result, type):
            model = phase.result
        else:
            model = results.resolve_result_model(
                phase.result, self.result_models, phase=phase.name
            )
```

with:

```python
        model = self._result_model(phase)
```

- [ ] **Step 6: Add `_result_model`, `adopt` and `_decline`**

In `src/agent_manager/dispatch.py`, insert after `AgentRunner._record_attempt` (after line 598, `        self.store.record_attempt(self.story_id, self.card_id, phase.name, attempt)`), still inside the class:

```python

    def _result_model(self, phase: phase_model.AgentPhase) -> type[BaseModel] | None:
        """The model this phase's result is validated against, or `None`.

        A declared phase carries its result model as the class itself, which
        is used as-is; a result given by name is looked up in
        `result_models`, and a name the table lacks is refused here.
        """
        if phase.result is None:
            return None
        if isinstance(phase.result, type):
            return phase.result
        return results.resolve_result_model(
            phase.result, self.result_models, phase=phase.name
        )

    def adopt(
        self,
        phase: phase_model.AgentPhase,
        context: Mapping[str, Any],
        *,
        source_run: str,
        floor: int,
    ) -> Adopted | None:
        """Reuse `source_run`'s recorded `ok` attempt instead of dispatching.

        Attempts are read from the source run's journal, never from the
        `attempts` projection, and only attempts numbered above `floor` with
        status `ok` qualify: an orphaned `started` one, even with a valid
        file on disk, never does. The highest such attempt's result file is
        re-read and its gates re-run against the resumed context. Nothing to
        adopt returns `None` silently; a recorded result that no longer holds
        up declines with a warning and writes no row. Only an adoption records
        anything: this phase `done`, in the current run.
        """
        model = self._result_model(phase)
        try:
            run = self.store.replay_journal(source_run)
        except (JournalError, ValidationError) as error:
            return self._decline(
                phase, None, source_run,
                f"its journal cannot be read: {_render_error(error)}",
            )
        found = _recorded_phase(run, self.card_id, phase.name)
        if found is None:
            return None
        candidates = [
            attempt
            for attempt in found.attempts
            if attempt.n > floor and attempt.status == "ok"
        ]
        if not candidates:
            return None
        attempt = max(candidates, key=lambda candidate: candidate.n)
        verdict = read_result(None if model is None else attempt.result_path, model)
        if verdict.status != "ok":
            return self._decline(
                phase, attempt.n, source_run, verdict.detail or verdict.status
            )
        failure = evaluate_gates(
            phase, gate_values(context, phase.name, verdict.result), self.warnings
        )
        if failure is not None:
            return self._decline(
                phase, attempt.n, source_run, failure.detail or failure.status
            )
        self._record_phase(
            phase, "done", found.started_at or self.clock(), self.clock(), None
        )
        self.warnings.append(
            f"phase {phase.name!r} was not dispatched again: attempt {attempt.n} of "
            f"run {source_run} had already succeeded (result reused)"
        )
        return Adopted(verdict.result, attempt.n, source_run)

    def _decline(
        self,
        phase: phase_model.AgentPhase,
        n: int | None,
        source_run: str,
        why: str,
    ) -> None:
        """Warn that a recorded attempt is not reused; write nothing.

        `n` is `None` when the journal could not be read, before any attempt
        was found; the number is then shown as `?`.
        """
        shown = "?" if n is None else n
        self.warnings.append(
            f"phase {phase.name!r}: attempt {shown} of run {source_run} was not "
            f"reused ({why}); dispatching again"
        )
        return None
```

- [ ] **Step 7: Run the dispatch tests**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: PASS, the whole file, including every pre-existing `__call__` and `classify` test.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "feat(dispatch): adopt a recorded ok attempt instead of dispatching again"
```

---

### Task 4: Full verification

**Files:** none modified.

**Interfaces:**
- Consumes: everything above.
- Produces: a green suite on the branch.

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, every test, including `tests/e2e` (the opt-in real-harness test is skipped by its own marker as usual) and `tests/runtime`.

- [ ] **Step 2: Confirm the scope fence**

Run: `git diff --stat m11/task-compute-the-floor-at-94088f7e...HEAD`
Expected: only `src/agent_manager/dispatch.py`, `src/agent_manager/store.py`, `tests/test_dispatch.py`, `tests/test_store.py` (plus this plan and the spec under `docs/superpowers/`). No change under `src/agent_manager/runtime/` and no `tests/runtime/test_exactly_once.py`.

- [ ] **Step 3: Confirm no pygents import entered dispatch**

Run: `grep -n "pygents" src/agent_manager/dispatch.py`
Expected: no output.
