"""The pool codec (pygents-engine design §5, §9).

Unit tier per design §14: pure codec and data-shaping code over in-memory
pygents containers -- no git repo, no board, no fake adapter, no model call.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from pygents import ContextItem, ContextPool, ContextQueue

from agent_manager import results
from agent_manager.runtime import context


def test_round_trip_through_json():
    value = {
        "worktree": Path("/r/.claude/worktrees/m6/x"),
        "at": datetime(2026, 9, 25, tzinfo=timezone.utc),
        "explore": results.ExploreResult(
            refused=False,
            reason=None,
            summary="s" * 80,
            verification=results.Verification(
                full_suite=["uv run pytest"], typecheck="", lint=[]
            ),
        ),
    }

    back = context.decode(json.loads(json.dumps(context.encode(value))))

    assert back == value


async def test_binding_table_rebuilds_seed_results_and_feedback():
    pool, memory = ContextPool(), ContextQueue(limit=10)
    await pool.add(context.seed_item({"branch": "m6/task-x-1234abcd", "worktree": Path("/w")}))
    await pool.add(
        ContextItem(
            id="spec",
            description="spec result",
            content=context.encode({"path": Path("docs/s.md"), "note": None}),
        )
    )
    await memory.append(
        ContextItem(content={"for": "spec", "from": "validate_spec", "detail": "no error path"})
    )
    await memory.append(
        ContextItem(content={"for": "plan", "from": "validate_plan", "detail": "other"})
    )

    table = context.binding_table(pool, memory, "spec")

    assert table["branch"] == "m6/task-x-1234abcd"
    assert table["worktree"] == Path("/w")
    assert table["spec"] == {"path": Path("docs/s.md"), "note": None}
    assert table["feedback"] == [
        {"for": "spec", "from": "validate_spec", "detail": "no error path"}
    ]
    assert table["feedback"][0] is not memory.items[0].content


async def test_binding_table_excludes_the_skipped_item():
    pool, memory = ContextPool(), ContextQueue(limit=10)
    await pool.add(context.seed_item({"branch": "b"}))
    await pool.add(
        ContextItem(id=context.SKIPPED, description="skipped phases", content=["critic"])
    )

    table = context.binding_table(pool, memory, "plan")

    assert table == {"branch": "b", "feedback": []}


async def test_binding_table_ignores_non_mapping_queue_items():
    pool, memory = ContextPool(), ContextQueue(limit=10)
    await pool.add(context.seed_item({"branch": "b"}))
    await memory.append(ContextItem(content="a free-text note"))
    await memory.append(ContextItem(content={"for": "plan", "from": "review", "detail": "d"}))

    table = context.binding_table(pool, memory, "plan")

    assert table["feedback"] == [{"for": "plan", "from": "review", "detail": "d"}]


def test_seed_item_is_the_reserved_subtask_item():
    item = context.seed_item({"worktree": Path("/w")})

    assert item.id == context.SUBTASK == "subtask"
    assert item.description == "fixed subtask context"
    assert item.content == {"worktree": {"$path": "/w"}}


def test_only_runtime_imports_pygents():
    import re

    import agent_manager

    src = Path(agent_manager.__file__).parent
    runtime = src / "runtime"
    pattern = re.compile(r"^\s*(from|import)\s+pygents\b", re.MULTILINE)
    offenders = [
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if runtime not in path.parents and pattern.search(path.read_text())
    ]

    assert offenders == []
