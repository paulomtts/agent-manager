"""Behaviour of the plan-check step (design §4 `steps/`, spec card d3feb87e).

Placement follows design §14: `plan_check.py` is a Steps component, so its
behaviour is exercised against real temporary directories and files created in
`tmp_path` -- no network, and no faking of the filesystem except where a test
must force an outcome a real filesystem will not produce on demand (an
unreadable file), exactly as the ported `plan-check.test.mjs` uses its `fakeFs`.
"""

from pathlib import Path

import pytest

from agent_manager.steps import plan_check
from agent_manager.steps.plan_check import VALIDATED_MARKER, matches_card, pick_plan


def test_the_marker_is_the_exact_literal_validate_writes():
    assert VALIDATED_MARKER == "<!-- task-pipeline: validated -->"


def test_a_plan_matches_when_the_short_id_is_its_final_segment():
    assert matches_card("task-write-rows-a32af745.md", "a32af745") is True
    assert matches_card("task-a32af745.md", "a32af745") is True


def test_one_cards_plan_never_answers_for_another():
    # The hazard the old non-digit boundary could not express: hex ids may be
    # preceded by hex characters.
    assert matches_card("task-deadbeefa32af745.md", "a32af745") is False
    assert matches_card("task-rows-a32af746.md", "a32af745") is False
    assert matches_card("task-rows-a32af745-old.md", "a32af745") is False


def test_only_md_files_match():
    assert matches_card("task-rows-a32af745.txt", "a32af745") is False
    assert matches_card("task-rows-a32af745", "a32af745") is False


def test_a_missing_or_non_string_filename_matches_nothing():
    assert matches_card(None, "a32af745") is False
    assert plan_check.matches_card("", "a32af745") is False


def test_the_newest_matching_plan_wins():
    # A re-planned subtask leaves the old file behind; the stale one must not
    # decide whether Spec/Plan/Validate re-run. "Newest" is lexicographic on
    # the date-prefixed filename -- no `stat` call is made.
    assert (
        pick_plan(
            [
                "2026-01-task-rows-a32af745.md",
                "2026-08-task-rows-a32af745.md",
                "task-other-deadbeef.md",
            ],
            "a32af745",
        )
        == "2026-08-task-rows-a32af745.md"
    )


def test_pick_plan_is_none_when_nothing_matches():
    assert pick_plan(["task-other-deadbeef.md"], "a32af745") is None
    assert pick_plan([], "a32af745") is None
    assert pick_plan(None, "a32af745") is None


CARD_UUID = "a32af745-0322-4a49-9dd9-44630af9632d"


def test_an_already_short_lowercase_id_passes_straight_through():
    assert plan_check._card_short_id("a32af745") == "a32af745"


def test_a_full_uuid_resolves_through_dag_short_id():
    assert plan_check._card_short_id(CARD_UUID) == "a32af745"
    assert plan_check._card_short_id(CARD_UUID.replace("-", "")) == "a32af745"
    # dag.short_id accepts either case in a full id and lowercases it, so an
    # uppercase FULL uuid is normalised rather than rejected (spec §3).
    assert plan_check._card_short_id(CARD_UUID.upper()) == "a32af745"


def test_a_card_mapping_or_object_resolves_via_its_id_field():
    class Card:
        id = CARD_UUID

    assert plan_check._card_short_id({"id": CARD_UUID}) == "a32af745"
    assert plan_check._card_short_id(Card()) == "a32af745"


def test_an_uppercase_or_malformed_card_id_raises_rather_than_matching_nothing():
    # Accepting it would turn a caller's typo into "no plan found" -- a gate
    # failing open in the direction that halts a run for a reason that is not
    # true.
    for bad in ["A32AF745", "42", "zzzzzzzz", "", "not-a-uuid", None, 42]:
        with pytest.raises(ValueError):
            plan_check._card_short_id(bad)
    with pytest.raises(ValueError):
        plan_check._card_short_id({"title": "no id here"})


def test_the_plans_dir_defaults_under_the_repo_and_an_explicit_one_wins():
    assert plan_check._plans_dir(None, "/abs/repo") == "/abs/repo/.claude/plans"
    assert plan_check._plans_dir("/p", "/abs/repo") == "/p"
    assert plan_check._plans_dir(Path("/p"), None) == "/p"


def test_neither_plans_dir_nor_repo_dir_is_a_caller_bug_not_a_first_run():
    with pytest.raises(ValueError, match="plans_dir"):
        plan_check._plans_dir(None, None)


def _plans(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """A real plans directory on disk holding `files` (filename -> content).

    Takes a plain dict rather than `**kwargs`: plan filenames contain `-` and
    `.`, neither of which is legal in a Python keyword.
    """
    directory = tmp_path / ".claude" / "plans"
    directory.mkdir(parents=True)
    for name, body in (files or {}).items():
        (directory / name).write_text(body, encoding="utf-8")
    return directory


def test_a_missing_plans_directory_is_a_normal_answer_not_a_failure(tmp_path: Path):
    # First run: nobody has planned anything yet. Real absent directory, no fake.
    got = plan_check.find_validated_plan("a32af745", repo_dir=tmp_path)
    assert got == {"found": False, "path": "", "validated": False}


def test_a_listable_directory_with_no_matching_plan_finds_nothing(tmp_path: Path):
    directory = _plans(tmp_path, {"task-rows-deadbeef.md": "# plan"})
    got = plan_check.find_validated_plan("a32af745", directory)
    assert got == {"found": False, "path": "", "validated": False}


def test_validated_only_when_the_marker_is_literally_present(tmp_path: Path):
    directory = _plans(
        tmp_path,
        {
            "task-rows-a32af745.md": f"# plan\n{VALIDATED_MARKER}\nsteps",
            "task-rows-deadbeef.md": "# plan\nno marker",
        },
    )
    validated = plan_check.find_validated_plan("a32af745", directory)
    assert validated == {
        "found": True,
        "path": str(directory / "task-rows-a32af745.md"),
        "validated": True,
    }
    assert plan_check.find_validated_plan("deadbeef", directory) == {
        "found": True,
        "path": str(directory / "task-rows-deadbeef.md"),
        "validated": False,
    }


def test_a_plan_that_merely_discusses_the_marker_still_counts(tmp_path: Path):
    # Documented deliberately: the check is a substring test, never a regex. A
    # plan quoting the marker in prose reads as validated. That is the accepted
    # cost of never mistaking a real marker for prose -- the failure that
    # matters.
    directory = _plans(
        tmp_path,
        {
            "task-rows-a32af745.md": (
                f"explains that {VALIDATED_MARKER} means signed off"
            )
        },
    )
    assert plan_check.find_validated_plan("a32af745", directory)["validated"] is True


def test_a_near_miss_marker_is_not_validated(tmp_path: Path):
    directory = _plans(
        tmp_path,
        {
            "task-rows-a32af745.md": (
                "<!-- task-pipeline: Validated -->\n"
                "<!--task-pipeline: validated-->\n"
                "<!-- task_pipeline: validated -->\n"
            )
        },
    )
    assert plan_check.find_validated_plan("a32af745", directory)["validated"] is False


def test_the_newest_plan_on_disk_decides(tmp_path: Path):
    directory = _plans(
        tmp_path,
        {
            "2026-01-task-rows-a32af745.md": f"# old\n{VALIDATED_MARKER}",
            "2026-08-task-rows-a32af745.md": "# re-planned, not yet signed off",
        },
    )
    got = plan_check.find_validated_plan("a32af745", directory)
    assert got["path"] == str(directory / "2026-08-task-rows-a32af745.md")
    assert got["validated"] is False


def test_the_card_may_arrive_as_a_uuid_or_a_mapping(tmp_path: Path):
    directory = _plans(
        tmp_path, {"task-rows-a32af745.md": f"# plan\n{VALIDATED_MARKER}"}
    )
    assert plan_check.find_validated_plan(CARD_UUID, directory)["validated"] is True
    assert (
        plan_check.find_validated_plan({"id": CARD_UUID}, directory)["validated"]
        is True
    )


def test_the_repo_dir_default_finds_the_plans_under_dot_claude(tmp_path: Path):
    _plans(tmp_path, {"task-rows-a32af745.md": f"{VALIDATED_MARKER}\n"})
    got = plan_check.find_validated_plan("a32af745", repo_dir=tmp_path)
    assert got["path"] == str(tmp_path / ".claude" / "plans" / "task-rows-a32af745.md")
    assert got["validated"] is True


def test_an_unreadable_plan_is_found_but_not_validated_and_says_why(tmp_path: Path):
    # The one case that fakes `read`: a reliably unreadable regular file cannot
    # be produced on demand (a test may run as root), exactly why the ported
    # `plan-check.test.mjs` reaches for its `fakeFs` here and nowhere else.
    directory = _plans(tmp_path, {"task-rows-a32af745.md": "# plan"})

    def refuse(path: str) -> str:
        raise PermissionError(13, "EACCES: permission denied")

    got = plan_check.find_validated_plan("a32af745", directory, read=refuse)
    assert got["found"] is True
    assert got["validated"] is False
    assert got["path"] == str(directory / "task-rows-a32af745.md")
    assert "EACCES" in got["error"]
    assert str(directory / "task-rows-a32af745.md") in got["error"]


def test_a_plan_that_is_not_valid_utf8_is_unreadable_not_a_crash(tmp_path: Path):
    # Decoding raises UnicodeDecodeError -- a ValueError, not an OSError -- so
    # catching OSError alone would let a corrupt file crash the run, which spec
    # §4 forbids ("no raised exceptions from I/O").
    directory = _plans(tmp_path)
    (directory / "task-rows-a32af745.md").write_bytes(b"\xff\xfe\x00plan")

    got = plan_check.find_validated_plan("a32af745", directory)
    assert got["found"] is True
    assert got["validated"] is False
    assert "could not read" in got["error"]


def test_a_directory_named_like_a_plan_is_unreadable_not_a_crash(tmp_path: Path):
    directory = _plans(tmp_path)
    (directory / "task-rows-a32af745.md").mkdir()

    got = plan_check.find_validated_plan("a32af745", directory)
    assert got["found"] is True
    assert got["validated"] is False
    assert "could not read" in got["error"]


def test_a_plans_dir_that_is_a_file_reports_no_plan(tmp_path: Path):
    not_a_directory = tmp_path / "plans"
    not_a_directory.write_text("this is a file", encoding="utf-8")

    got = plan_check.find_validated_plan("a32af745", not_a_directory)
    assert got == {"found": False, "path": "", "validated": False}


def test_a_successful_read_carries_no_error_key(tmp_path: Path):
    directory = _plans(tmp_path, {"task-rows-a32af745.md": VALIDATED_MARKER})
    assert "error" not in plan_check.find_validated_plan("a32af745", directory)


def test_the_gate_wants_both_found_and_validated(tmp_path: Path):
    directory = _plans(
        tmp_path,
        {
            "task-rows-a32af745.md": f"# plan\n{VALIDATED_MARKER}",
            "task-rows-deadbeef.md": "# plan\nno marker",
        },
    )
    signed_off = plan_check.find_validated_plan("a32af745", directory)
    unsigned = plan_check.find_validated_plan("deadbeef", directory)
    absent = plan_check.find_validated_plan("cafed00d", directory)

    assert plan_check.has_validated_plan(signed_off) is True
    assert plan_check.has_validated_plan(unsigned) is False
    assert plan_check.has_validated_plan(absent) is False


def test_the_gate_is_closed_for_an_unreadable_plan(tmp_path: Path):
    directory = _plans(tmp_path, {"task-rows-a32af745.md": VALIDATED_MARKER})

    def refuse(path: str) -> str:
        raise PermissionError(13, "EACCES: permission denied")

    unreadable = plan_check.find_validated_plan("a32af745", directory, read=refuse)
    assert plan_check.has_validated_plan(unreadable) is False


def test_the_gate_is_closed_for_anything_that_is_not_a_result_dict():
    # A phase that failed before producing a result must not read as "skip to
    # implement": the gate fails closed, so the pipeline re-plans.
    assert plan_check.has_validated_plan(None) is False
    assert plan_check.has_validated_plan({}) is False
    assert plan_check.has_validated_plan("found") is False
