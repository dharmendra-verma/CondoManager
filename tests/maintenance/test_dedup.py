"""Duplicate-detection unit tests (CM-31 AC2)."""

from __future__ import annotations

from datetime import timedelta

from agents.maintenance import dedup
from agents.maintenance.schema import TicketStatus


def test_extract_unit_variants() -> None:
    assert dedup.extract_unit("leak in unit 4B") == "4b"
    assert dedup.extract_unit("apt 12 has no power") == "12"
    assert dedup.extract_unit("problem in apartment 3c") == "3c"
    assert dedup.extract_unit("suite 200 door broken") == "200"
    assert dedup.extract_unit("#7a is flooding") == "7a"
    assert dedup.extract_unit("the lobby light is out") == dedup.UNKNOWN_UNIT


def test_categorize_buckets() -> None:
    assert dedup.categorize("the sink is leaking") == "plumbing"
    assert dedup.categorize("power outlet not working") == "electrical"
    assert dedup.categorize("the heater is broken") == "hvac"
    assert dedup.categorize("dishwasher won't start") == "appliance"
    assert dedup.categorize("front door lock jammed") == "structural"
    assert dedup.categorize("noise from upstairs") == "general"


def test_similarity_paraphrase_high_distinct_low() -> None:
    paraphrase = dedup.similarity(
        "kitchen sink leaking under cabinet", "the sink in my kitchen is leaking"
    )
    distinct = dedup.similarity("toilet keeps clogging", "kitchen sink leaking")
    assert paraphrase >= dedup.SIMILARITY_THRESHOLD
    assert distinct < dedup.SIMILARITY_THRESHOLD


def test_duplicate_pair_positive() -> None:
    assert dedup.is_duplicate_pair(
        existing_unit="4b",
        existing_issue="kitchen sink leaking under cabinet",
        candidate_unit="4b",
        candidate_issue="sink in kitchen is leaking",
        age_days=2,
    )


def test_duplicate_pair_unknown_unit_never_matches() -> None:
    assert not dedup.is_duplicate_pair(
        existing_unit=dedup.UNKNOWN_UNIT,
        existing_issue="kitchen sink leaking",
        candidate_unit=dedup.UNKNOWN_UNIT,
        candidate_issue="kitchen sink leaking",
        age_days=1,
    )


def test_duplicate_pair_outside_window() -> None:
    assert not dedup.is_duplicate_pair(
        existing_unit="4b",
        existing_issue="kitchen sink leaking",
        candidate_unit="4b",
        candidate_issue="kitchen sink leaking",
        age_days=8,
    )


def test_duplicate_pair_different_category() -> None:
    assert not dedup.is_duplicate_pair(
        existing_unit="4b",
        existing_issue="power outlet not working",
        candidate_unit="4b",
        candidate_issue="kitchen sink leaking",
        age_days=1,
    )


def test_find_open_duplicate_skips_resolved(make_ticket, now) -> None:  # noqa: ANN001
    resolved = make_ticket(
        ticket_id="TKT-OLD1",
        status=TicketStatus.RESOLVED,
        created_at=now - timedelta(days=1),
        issue_text="kitchen sink leaking under cabinet",
    )
    assert (
        dedup.find_open_duplicate(
            unit="4b",
            issue_text="sink in kitchen is leaking",
            existing=[resolved],
            now=now,
        )
        is None
    )


def test_find_open_duplicate_matches_open(make_ticket, now) -> None:  # noqa: ANN001
    open_t = make_ticket(
        ticket_id="TKT-OPEN1",
        status=TicketStatus.NEW,
        created_at=now - timedelta(days=1),
        issue_text="kitchen sink leaking under cabinet",
    )
    match = dedup.find_open_duplicate(
        unit="4b",
        issue_text="sink in kitchen is leaking",
        existing=[open_t],
        now=now,
    )
    assert match is not None
    assert match.id == "TKT-OPEN1"


def test_is_repeat_includes_resolved(make_ticket, now) -> None:  # noqa: ANN001
    resolved = make_ticket(
        status=TicketStatus.RESOLVED,
        created_at=now - timedelta(days=3),
        issue_text="kitchen sink leaking",
    )
    assert dedup.is_repeat(
        unit="4b",
        issue_text="sink leaking again",
        existing=[resolved],
        now=now,
    )
