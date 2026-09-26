"""The pool codec (pygents-engine design §5, §9).

Unit tier per design §14: pure codec and data-shaping code over in-memory
pygents containers -- no git repo, no board, no fake adapter, no model call.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

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
