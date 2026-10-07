"""The generic limit seam: nothing outside the Claude adapter knows its wording."""

from datetime import datetime, timezone
from pathlib import Path

from agent_manager.harness import limits

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_FILES = {"claude.py", "claude_limits.py"}


def test_generic_modules_neither_import_nor_quote_the_claude_limit_parser():
    offenders = []
    sources = [*(ROOT / "src" / "agent_manager").rglob("*.py"), ROOT / "README.md"]
    for path in sources:
        if path.name in ADAPTER_FILES:
            continue
        text = path.read_text(encoding="utf-8").lower()
        if "claude_limits" in text or "hit your" in text:
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_read_tail_returns_only_the_end_and_none_for_a_missing_file(tmp_path):
    log = tmp_path / "stdout.log"
    log.write_text("head\n" + "x" * 40000 + "\ntail\n", encoding="utf-8")

    tail = limits.read_tail(log)

    assert tail.endswith("tail\n")
    assert "head" not in tail
    assert limits.read_tail(tmp_path / "absent.log") is None


def test_a_limit_hit_is_plain_data():
    hit = limits.LimitHit("other", datetime(2026, 10, 7, tzinfo=timezone.utc), "raw")

    assert (hit.kind, hit.raw) == ("other", "raw")
