"""`parse_limit` over the exact lines real runs logged (2026-10-05 to 2026-10-07)."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from agent_manager.harness import limits

SP = ZoneInfo("America/Sao_Paulo")
NOON_ISH = datetime(2026, 10, 7, 13, 0, tzinfo=SP)


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("line", "kind", "resets_at"),
    [
        ("You've hit your session limit · resets 10pm (America/Sao_Paulo)", "session", _utc(2026, 10, 8, 1)),
        ("You've hit your session limit · resets 2:30pm (America/Sao_Paulo)", "session", _utc(2026, 10, 7, 17, 30)),
        ("You've hit your session limit · resets 7:30pm (America/Sao_Paulo)", "session", _utc(2026, 10, 7, 22, 30)),
        ("You've hit your session limit · resets 12pm (America/Sao_Paulo)", "session", _utc(2026, 10, 8, 15)),
        ("You've hit your weekly limit · resets Oct 9, 8pm (America/Sao_Paulo)", "weekly", _utc(2026, 10, 9, 23)),
        ("You’ve hit your session limit · resets 10pm (America/Sao_Paulo)", "session", _utc(2026, 10, 8, 1)),
        ("You've hit your session limit · resets 10pm (UTC)", "session", _utc(2026, 10, 7, 22)),
    ],
)
def test_the_real_lines(line, kind, resets_at):
    hit = limits.parse_limit(line + "\n", NOON_ISH)

    assert hit == limits.LimitHit(kind, resets_at, line)


def test_a_reset_time_already_past_today_means_tomorrow():
    late = datetime(2026, 10, 7, 23, 30, tzinfo=SP)

    midnight = limits.parse_limit("You've hit your session limit · resets 12am (America/Sao_Paulo)", late)
    passed = limits.parse_limit("You've hit your session limit · resets 11pm (America/Sao_Paulo)", late)
    tonight = limits.parse_limit("You've hit your session limit · resets 11:45pm (America/Sao_Paulo)", late)

    assert midnight.resets_at == _utc(2026, 10, 8, 3)
    assert passed.resets_at == _utc(2026, 10, 9, 2)
    assert tonight.resets_at == _utc(2026, 10, 8, 2, 45)


def test_the_anchor_is_read_in_the_named_zone_not_utc():
    # 01:00Z on Oct 8 is still Oct 7 22:00 in Sao Paulo, so "11pm" is tonight.
    anchor = datetime(2026, 10, 8, 1, 0, tzinfo=timezone.utc)

    hit = limits.parse_limit("You've hit your session limit · resets 11pm (America/Sao_Paulo)", anchor)

    assert hit.resets_at == _utc(2026, 10, 8, 2)


def test_a_date_already_past_this_year_means_next_year():
    anchor = datetime(2026, 12, 30, 12, 0, tzinfo=SP)

    hit = limits.parse_limit("You've hit your weekly limit · resets Jan 2, 8am (America/Sao_Paulo)", anchor)

    assert hit.resets_at == _utc(2027, 1, 2, 11)


@pytest.mark.parametrize(
    "line",
    [
        "You've hit your session limit · resets 10pm (Mars/Olympus_Mons)",
        "You've hit your session limit · resets 10pm",
        "You've hit your session limit",
        "You've hit your session limit · resets soon (America/Sao_Paulo)",
        "You've hit your session limit · resets 25pm (America/Sao_Paulo)",
        "You've hit your weekly limit · resets Foo 9, 8pm (America/Sao_Paulo)",
        "You've hit your weekly limit · resets Feb 30, 8pm (America/Sao_Paulo)",
    ],
)
def test_an_unusable_reset_is_a_hit_with_no_time(line):
    hit = limits.parse_limit(line, NOON_ISH)

    assert hit is not None
    assert hit.resets_at is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "garbage\n\x00\x01 resets 10pm (America/Sao_Paulo)",
        "The plan stopped on \"You've hit your session limit\" earlier.",
        "I've written the result file. You've hit your session limit · resets 10pm (UTC)",
        "Permission allow rule: Write(.claude/**) is not matched",
    ],
)
def test_text_that_is_not_the_limit_line_is_none(text):
    assert limits.parse_limit(text, NOON_ISH) is None


def test_the_limit_line_among_other_output_is_found_and_the_last_one_wins():
    text = (
        "Permission allow rule (settings.json): Write(.claude/**) is not matched\n"
        "You've hit your session limit · resets 2:30pm (America/Sao_Paulo)\n"
        "You've hit your weekly limit · resets Oct 9, 8pm (America/Sao_Paulo)\n"
    )

    assert limits.parse_limit(text, NOON_ISH).kind == "weekly"


def test_read_limit_reads_only_the_tail_and_tolerates_a_missing_file(tmp_path):
    log = tmp_path / "stdout.log"
    log.write_text(
        "You've hit your session limit · resets 10pm (UTC)\n" + "x" * 40000 + "\n",
        encoding="utf-8",
    )

    assert limits.read_limit(log, lambda: NOON_ISH) is None
    log.write_text("y" * 40000 + "\nYou've hit your session limit · resets 10pm (UTC)\n")
    assert limits.read_limit(log, lambda: NOON_ISH).kind == "session"
    assert limits.read_limit(tmp_path / "absent.log", lambda: NOON_ISH) is None
