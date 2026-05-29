"""Priority + ETA tests (CM-31 AC3 / AC4 ETA)."""

from __future__ import annotations

import pytest
from agents.maintenance.priority import assign_priority, estimate_eta
from agents.maintenance.schema import Priority
from agents.orchestrator.state import Tone, Urgency


@pytest.mark.parametrize(
    ("urgency", "expected"),
    [
        (Urgency.EMERGENCY, Priority.P1),
        (Urgency.HIGH, Priority.P2),
        (Urgency.MEDIUM, Priority.P3),
        (Urgency.LOW, Priority.P4),
        (None, Priority.P3),  # CM-30 not merged -> urgency None -> MEDIUM
    ],
)
def test_base_priority_from_urgency(urgency: Urgency | None, expected: Priority) -> None:
    assert assign_priority(urgency, None, is_repeat=False) == expected


def test_tone_bumps_one_band() -> None:
    assert assign_priority(Urgency.MEDIUM, Tone.ANGRY, is_repeat=False) == Priority.P2
    assert assign_priority(Urgency.MEDIUM, Tone.URGENT, is_repeat=False) == Priority.P2
    assert assign_priority(Urgency.MEDIUM, Tone.NEUTRAL, is_repeat=False) == Priority.P3


def test_repeat_bumps_one_band() -> None:
    assert assign_priority(Urgency.LOW, None, is_repeat=True) == Priority.P3


def test_bumps_clamp_at_p1() -> None:
    # emergency + angry + repeat would underflow; must clamp at P1.
    assert assign_priority(Urgency.EMERGENCY, Tone.ANGRY, is_repeat=True) == Priority.P1


def test_tone_and_repeat_stack() -> None:
    assert assign_priority(Urgency.LOW, Tone.ANGRY, is_repeat=True) == Priority.P2


def test_eta_table() -> None:
    assert estimate_eta(Priority.P1, "plumbing") == "within 2 hours"
    assert estimate_eta(Priority.P4, "general") == "within 5 business days"
