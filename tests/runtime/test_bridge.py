"""The bridge between pygents' event loop and blocking engine code (pygents-engine design G2).

Unit tier: the children are `sys.executable -c` sleepers, as in
tests/harness/test_launcher.py -- never a harness. The runners are plain
functions that know only the arguments they are handed.
"""

import asyncio
import sys
import threading

import pytest

from agent_manager.errors import AgentPhaseFailed
from agent_manager.harness import launcher
from agent_manager.runtime import bridge

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


async def test_cancelling_a_call_kills_its_process(tmp_path):
    # Review Focus 1 of the parent plan: `to_thread` cannot be cancelled, so the
    # bridge has to kill what the abandoned thread started.
    started = threading.Event()

    def runner(phase, context, rendered):
        hook = bridge.current_spawn_hook()

        def on_spawn(process):
            hook(process)
            started.set()

        return launcher.run_direct(
            SLEEPER, cwd=tmp_path, timeout=120, stdout_path=tmp_path / "out",
            on_spawn=on_spawn,
        )

    task = asyncio.create_task(bridge.call_agent(runner, None, {}, None))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bridge.last_spawned_for_tests().poll() is not None


async def test_cancelling_one_call_leaves_another_calls_process_alone(tmp_path):
    events = {"a": threading.Event(), "b": threading.Event()}
    procs = {}

    def make_runner(key):
        def runner(phase, context, rendered):
            hook = bridge.current_spawn_hook()

            def on_spawn(process):
                procs[key] = process
                hook(process)
                events[key].set()

            return launcher.run_direct(
                SLEEPER, cwd=tmp_path, timeout=120,
                stdout_path=tmp_path / f"{key}.log", on_spawn=on_spawn,
            )

        return runner

    a = asyncio.create_task(bridge.call_agent(make_runner("a"), None, {}, None))
    b = asyncio.create_task(bridge.call_agent(make_runner("b"), None, {}, None))
    assert await asyncio.to_thread(events["a"].wait, 5)
    assert await asyncio.to_thread(events["b"].wait, 5)
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    try:
        assert procs["a"].poll() is not None
        assert procs["b"].poll() is None
    finally:
        launcher.kill_tree(procs["b"])
        await b


async def test_a_process_spawned_after_the_call_was_cancelled_is_killed_at_once(tmp_path):
    # Review Focus 1: the cancel lands while the worker is still before Popen.
    go, finished = threading.Event(), threading.Event()
    seen = []

    def runner(phase, context, rendered):
        go.wait(5)
        hook = bridge.current_spawn_hook()
        try:
            return launcher.run_direct(
                SLEEPER, cwd=tmp_path, timeout=120, stdout_path=tmp_path / "out",
                on_spawn=lambda process: (seen.append(process), hook(process)),
            )
        finally:
            finished.set()

    task = asyncio.create_task(bridge.call_agent(runner, None, {}, None))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    go.set()
    assert await asyncio.to_thread(finished.wait, 10)
    assert len(seen) == 1
    assert seen[0].poll() is not None


async def test_a_call_returns_the_runners_result_and_binds_a_hook_only_inside_it():
    seen = []

    def runner(phase, context, rendered):
        seen.append(bridge.current_spawn_hook())
        return {"phase": phase, "context": context, "rendered": rendered}

    result = await bridge.call_agent(runner, "explore", {"k": 1}, "brief")

    assert result == {"phase": "explore", "context": {"k": 1}, "rendered": "brief"}
    assert callable(seen[0])
    assert bridge.current_spawn_hook() is None
    assert await asyncio.to_thread(bridge.current_spawn_hook) is None


async def test_a_runner_exception_propagates_unchanged():
    failure = AgentPhaseFailed("review", outcome="gate_failed", detail="blocked")

    def runner(phase, context, rendered):
        raise failure

    with pytest.raises(AgentPhaseFailed) as caught:
        await bridge.call_agent(runner, None, {}, None)
    assert caught.value is failure


async def test_call_step_runs_the_function_off_the_event_loop():
    loop_thread = threading.get_ident()

    def fn(x):
        return x, threading.get_ident()

    value, thread = await bridge.call_step(fn, {"x": 1})

    assert value == 1
    assert thread != loop_thread
