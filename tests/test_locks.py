"""Unit tests for `agent_manager.locks` (spec X7, X8; plan Task 1.1).

Cross-process order comes from pipes and exit codes, thread order from
`threading.Event`; no test sleeps to establish ordering. The holder and probe
helpers live in `tests/lockhelpers.py`, shared with the Wiring tests. Children
inherit the test's `XDG_DATA_HOME` (tests/conftest.py), so they flock the same
files.
"""

from __future__ import annotations

import inspect
import os
import threading
import time
from pathlib import Path

import pytest
from lockhelpers import _holder, _probe, _reap, _release

from agent_manager import locks, paths


def _free_to_another_thread(local: threading.RLock) -> bool:
    """Whether a different thread can take `local` right now (never blocks)."""
    outcome: list[bool] = []

    def take() -> None:
        got = local.acquire(blocking=False)
        if got:
            local.release()
        outcome.append(got)

    worker = threading.Thread(target=take)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive()
    return outcome[0]


# --- the seven plan tests -------------------------------------------------


def test_a_lock_held_by_another_process_times_out_then_is_acquired(tmp_path):
    child = _holder(tmp_path, "git")
    try:
        lock = locks.project_lock(tmp_path, "git")
        with pytest.raises(locks.LockTimeoutError) as caught:
            lock.acquire(timeout=0)
        assert caught.value.path == paths.project_lock_path(tmp_path, "git")
        _release(child)
        lock.acquire(timeout=0)
        lock.release()
    finally:
        _reap(child)


def test_a_killed_holder_releases_the_lock(tmp_path):  # Review Focus 5 (plan)
    child = _holder(tmp_path, "board")
    try:
        child.kill()
        child.wait()
        lock = locks.project_lock(tmp_path, "board")
        lock.acquire(timeout=0)
        lock.release()
    finally:
        _reap(child)


def test_nesting_on_one_thread_takes_one_flock(tmp_path):  # Review Focus 1 (plan)
    lock = locks.project_lock(tmp_path, "board")
    with lock:
        with lock:
            assert _probe(tmp_path, "board") == "busy"
        assert _probe(tmp_path, "board") == "busy"
    assert _probe(tmp_path, "board") == "free"


def test_two_threads_serialise_on_the_in_process_layer(tmp_path):
    lock = locks.project_lock(tmp_path, "board")
    events: list[str] = []
    failures: list[BaseException] = []
    refused = threading.Event()

    def second() -> None:
        try:
            try:
                lock.acquire(timeout=0)
            except locks.LockTimeoutError:
                events.append("second refused")
            else:
                lock.release()
                events.append("second got it while the first held it")
            refused.set()
            lock.acquire()
            events.append("second acquired")
            assert _probe(tmp_path, "board") == "busy"
            lock.release()
        except BaseException as error:  # surfaced to the main thread below
            failures.append(error)
            refused.set()

    lock.acquire()
    worker = threading.Thread(target=second)
    worker.start()
    assert refused.wait(timeout=30)
    events.append("first releasing")
    lock.release()
    worker.join(timeout=30)

    assert not worker.is_alive()
    assert failures == []
    assert events == ["second refused", "first releasing", "second acquired"]
    assert _probe(tmp_path, "board") == "free"


def test_holding_one_project_lock_and_asking_for_another_is_refused(tmp_path):
    board_local = threading.RLock()
    git = locks.project_lock(tmp_path, "git")
    board = locks.project_lock(tmp_path, "board", local=board_local)
    with git:
        with pytest.raises(locks.LockOrderError) as caught:
            board.acquire()
        assert str(git.path) in str(caught.value)
        # Review Focus 4: the refusal took neither layer of `board`.
        assert _probe(tmp_path, "board") == "free"
        assert _free_to_another_thread(board_local)
    board.acquire(timeout=0)
    board.release()


def test_the_lock_file_is_under_the_data_directory_not_the_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with locks.project_lock(repo, "board"):
        pass
    lock_file = paths.project_lock_path(repo, "board")
    assert lock_file.exists()  # kept after release; never deleted
    assert lock_file.is_relative_to(paths.data_dir())
    assert not lock_file.is_relative_to(repo)
    assert list(repo.iterdir()) == []


def test_a_timeout_releases_the_in_process_layer(tmp_path):
    local = threading.RLock()
    child = _holder(tmp_path, "git")
    try:
        lock = locks.project_lock(tmp_path, "git", local=local)
        with pytest.raises(locks.LockTimeoutError):
            lock.acquire(timeout=0)
        assert _free_to_another_thread(local)
        _release(child)
        lock.acquire(timeout=0)
        lock.release()
    finally:
        _reap(child)


# --- timeouts --------------------------------------------------------------


def test_a_positive_timeout_polls_until_it_runs_out(tmp_path):
    local = threading.RLock()
    child = _holder(tmp_path, "git")
    try:
        lock = locks.project_lock(tmp_path, "git", local=local)
        with pytest.raises(locks.LockTimeoutError) as caught:
            lock.acquire(timeout=0.2)
        assert caught.value.path == paths.project_lock_path(tmp_path, "git")
        assert caught.value.timeout == 0.2
        assert _free_to_another_thread(local)
    finally:
        _reap(child)


def test_a_positive_timeout_retries_and_gets_the_lock_once_it_is_freed(
    tmp_path, monkeypatch
):
    # The first failed flock attempt asks `_backoff` for a delay; only then is
    # the holder told to release, so the lock can only be obtained by a retry.
    first_miss = threading.Event()
    real_backoff = locks._backoff

    def signalling_backoff(attempt: int) -> float:
        first_miss.set()
        return real_backoff(attempt)

    monkeypatch.setattr(locks, "_backoff", signalling_backoff)
    child = _holder(tmp_path, "git")
    outcome: list[BaseException | None] = []
    lock = locks.project_lock(tmp_path, "git")

    def take() -> None:
        try:
            lock.acquire(timeout=30)
        except BaseException as error:  # surfaced to the main thread below
            outcome.append(error)
        else:
            outcome.append(None)
            lock.release()

    try:
        worker = threading.Thread(target=take)
        worker.start()
        assert first_miss.wait(timeout=30)
        _release(child)
        worker.join(timeout=30)
        assert not worker.is_alive()
        assert outcome == [None]
    finally:
        _reap(child)


def test_the_timeout_bounds_the_wait_across_both_layers(tmp_path):
    # `local` is held by another thread for most of the timeout, and the flock
    # by another process for all of it: the two waits share one budget.
    local = threading.RLock()
    held = threading.Event()
    done = threading.Event()

    def hog() -> None:
        with local:
            held.set()
            threading.Event().wait(0.8)
        done.set()

    child = _holder(tmp_path, "git")
    worker = threading.Thread(target=hog)
    try:
        lock = locks.project_lock(tmp_path, "git", local=local)
        worker.start()
        assert held.wait(timeout=30)
        start = time.monotonic()
        with pytest.raises(locks.LockTimeoutError) as caught:
            lock.acquire(timeout=1.0)
        elapsed = time.monotonic() - start
        assert done.is_set()  # the in-process wait really was spent
        assert elapsed < 1.5
        assert caught.value.timeout == 1.0
        assert _free_to_another_thread(local)
    finally:
        worker.join(timeout=30)
        _reap(child)


def test_the_instance_timeout_applies_when_acquire_gets_none(tmp_path):
    child = _holder(tmp_path, "git")
    try:
        lock = locks.ProcessLock(paths.project_lock_path(tmp_path, "git"), timeout=0)
        with pytest.raises(locks.LockTimeoutError):
            lock.acquire()
    finally:
        _reap(child)


def test_timeout_default_is_ten_minutes():
    assert locks.LOCK_TIMEOUT_SECONDS == 600.0
    signature = inspect.signature(locks.ProcessLock)
    assert signature.parameters["timeout"].default == locks.LOCK_TIMEOUT_SECONDS
    lock = locks.ProcessLock(Path("/nonexistent/x.lock"))
    assert lock.path == Path("/nonexistent/x.lock")


def test_backoff_doubles_then_caps_and_never_overflows():  # Review Focus 1
    assert [locks._backoff(n) for n in range(5)] == pytest.approx(
        [0.05, 0.1, 0.2, 0.4, 0.5]
    )
    assert locks._backoff(1024) == 0.5
    assert locks._backoff(10_000) == 0.5


def test_the_errors_are_runtime_errors():
    assert issubclass(locks.LockTimeoutError, RuntimeError)
    assert issubclass(locks.LockOrderError, RuntimeError)
    error = locks.LockTimeoutError(Path("/x.lock"), 1.5)
    assert error.path == Path("/x.lock")
    assert "/x.lock" in str(error)


# --- release and failure paths ----------------------------------------------


def test_release_by_a_thread_that_does_not_hold_it_is_refused(tmp_path):  # RF 2
    lock = locks.project_lock(tmp_path, "board")
    with pytest.raises(RuntimeError):
        lock.release()

    failures: list[BaseException] = []

    def stray_release() -> None:
        try:
            lock.release()
        except BaseException as error:
            failures.append(error)

    with lock:
        worker = threading.Thread(target=stray_release)
        worker.start()
        worker.join(timeout=30)
        assert not worker.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], RuntimeError)
        assert _probe(tmp_path, "board") == "busy"
    assert _probe(tmp_path, "board") == "free"


def test_an_exception_in_the_body_releases_both_layers(tmp_path):  # RF 3
    local = threading.RLock()
    lock = locks.project_lock(tmp_path, "board", local=local)
    with pytest.raises(KeyError):
        with lock:
            with lock:
                raise KeyError("boom")
    assert _probe(tmp_path, "board") == "free"
    assert _free_to_another_thread(local)
    lock.acquire(timeout=0)
    lock.release()


def test_a_flock_failure_releases_the_in_process_layer(tmp_path):
    local = threading.RLock()
    lock = locks.ProcessLock(tmp_path / "missing-dir" / "x.lock", local=local)
    with pytest.raises(FileNotFoundError):
        lock.acquire(timeout=0)
    assert _free_to_another_thread(local)


def test_the_lock_descriptor_is_not_inheritable(tmp_path):
    lock = locks.project_lock(tmp_path, "git")
    with lock:
        assert os.get_inheritable(lock._fd) is False


# --- the registry ------------------------------------------------------------


def test_project_lock_is_one_object_per_resolved_path(tmp_path, monkeypatch):  # RF 5
    repo = tmp_path / "repo"
    repo.mkdir()
    link = tmp_path / "link"
    link.symlink_to(repo)
    monkeypatch.chdir(tmp_path)

    lock = locks.project_lock(repo, "board")
    assert locks.project_lock(repo, "board") is lock
    assert locks.project_lock(link, "board") is lock
    assert locks.project_lock(Path("repo"), "board") is lock
    assert locks.project_lock(repo, "git") is not lock
    assert lock.path == paths.project_lock_path(repo, "board")

    with locks.project_lock(repo, "board"):
        with locks.project_lock(link, "board"):  # re-enters: no second flock
            assert _probe(repo, "board") == "busy"
    assert _probe(repo, "board") == "free"


def test_project_lock_refuses_a_different_local_for_a_registered_path(tmp_path):
    local = threading.RLock()
    lock = locks.project_lock(tmp_path, "board", local=local)
    assert locks.project_lock(tmp_path, "board", local=local) is lock
    assert locks.project_lock(tmp_path, "board") is lock  # None means "whichever"
    with pytest.raises(ValueError):
        locks.project_lock(tmp_path, "board", local=threading.RLock())


def test_project_lock_refuses_an_explicit_local_after_a_default_one(tmp_path):
    locks.project_lock(tmp_path, "git")
    with pytest.raises(ValueError):
        locks.project_lock(tmp_path, "git", local=threading.RLock())
