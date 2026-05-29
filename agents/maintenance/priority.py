"""Priority + ETA assignment (CM-31 AC3 + AC4 ETA).

Priority is derived from three deterministic inputs:

* **urgency** (from CM-30 Triage; ``None`` until CM-30 lands -> treated as
  ``MEDIUM``),
* **tone** (``ANGRY`` / ``URGENT`` bump one band; ``None`` -> ``NEUTRAL``),
* **repeat-status** (a recurring same-unit/category issue bumps one band).

Bumps lower the band number (P3 -> P2 -> P1) and clamp at P1, so an
emergency stays P1 no matter what.
"""

from __future__ import annotations

from agents.orchestrator.state import Tone, Urgency

from .schema import Priority

#: Base band purely from urgency. ``None`` urgency maps to MEDIUM so the
#: agent works today, before CM-30 Triage populates the field.
_URGENCY_BASE: dict[Urgency, Priority] = {
    Urgency.EMERGENCY: Priority.P1,
    Urgency.HIGH: Priority.P2,
    Urgency.MEDIUM: Priority.P3,
    Urgency.LOW: Priority.P4,
}

#: Tones that justify bumping a tenant's ticket up one band.
_ESCALATING_TONES = frozenset({Tone.ANGRY, Tone.URGENT})

#: Ordered most-urgent-first so index == rank-1.
_ORDER: tuple[Priority, ...] = (Priority.P1, Priority.P2, Priority.P3, Priority.P4)

#: SLA ETA copy per band (CM-31 AC4 confirmation includes an ETA).
_ETA_BY_PRIORITY: dict[Priority, str] = {
    Priority.P1: "within 2 hours",
    Priority.P2: "within 24 hours",
    Priority.P3: "within 2 business days",
    Priority.P4: "within 5 business days",
}


def assign_priority(
    urgency: Urgency | None,
    tone: Tone | None,
    *,
    is_repeat: bool,
) -> Priority:
    """Compute the ticket priority band.

    Args:
        urgency: Triage urgency, or ``None`` (-> MEDIUM) until CM-30 lands.
        tone: Triage tone, or ``None`` (-> neutral). Angry/urgent bumps once.
        is_repeat: Whether this is a recurring issue (bumps once).
    """
    base = _URGENCY_BASE[urgency or Urgency.MEDIUM]
    rank = _ORDER.index(base)  # 0..3
    if tone in _ESCALATING_TONES:
        rank -= 1
    if is_repeat:
        rank -= 1
    rank = max(0, rank)  # clamp at P1
    return _ORDER[rank]


def estimate_eta(priority: Priority, category: str) -> str:
    """Human-readable ETA for the tenant confirmation.

    ``category`` is accepted for future per-category SLA tuning; v1 keys the
    ETA off the priority band only.
    """
    return _ETA_BY_PRIORITY[priority]
