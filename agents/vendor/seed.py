"""Seed vendor roster for the offline repository + matching tests (CM-35).

A small, hand-curated set covering the CM-31 maintenance categories. Each
common category has at least one pre-approved, 7-day-available, high-performing
vendor so the graph path is deterministic regardless of the real weekday;
secondary vendors with narrower availability / lower scores / no pre-approval
exercise ranking and the approval path.
"""

from __future__ import annotations

from .schema import CostTier, Vendor

_ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]


def seed_vendors() -> list[Vendor]:
    """Return a fresh list of seeded vendors (new objects each call)."""
    return [
        Vendor(
            id="v-plumb-1",
            name="AquaFix Plumbing",
            categories=["plumbing"],
            available_days=list(_ALL_DAYS),
            performance_score=0.92,
            cost_tier=CostTier.LOW,
            pre_approved=True,
            contact_email="dispatch@aquafix.example",
            contact_sms="+15550000001",
        ),
        Vendor(
            id="v-plumb-2",
            name="Premium Pipes",
            categories=["plumbing"],
            available_days=list(_WEEKDAYS),
            performance_score=0.97,
            cost_tier=CostTier.HIGH,
            pre_approved=False,
            contact_email="ops@premiumpipes.example",
        ),
        Vendor(
            id="v-elec-1",
            name="BoltWorks Electric",
            categories=["electrical"],
            available_days=list(_ALL_DAYS),
            performance_score=0.88,
            cost_tier=CostTier.MEDIUM,
            pre_approved=True,
            contact_email="jobs@boltworks.example",
        ),
        Vendor(
            id="v-hvac-1",
            name="ClimateCare HVAC",
            categories=["hvac"],
            available_days=list(_ALL_DAYS),
            performance_score=0.90,
            cost_tier=CostTier.MEDIUM,
            pre_approved=True,
            contact_email="service@climatecare.example",
        ),
        Vendor(
            id="v-appliance-1",
            name="FixIt Appliances",
            categories=["appliance"],
            available_days=list(_ALL_DAYS),
            performance_score=0.85,
            cost_tier=CostTier.LOW,
            pre_approved=True,
            contact_email="help@fixit.example",
        ),
        Vendor(
            id="v-struct-1",
            name="SolidBuild Structural",
            categories=["structural"],
            available_days=list(_ALL_DAYS),
            performance_score=0.95,
            cost_tier=CostTier.HIGH,
            pre_approved=True,  # still routes to approval — structural is safety-gated
            contact_email="build@solidbuild.example",
        ),
        Vendor(
            id="v-general-1",
            name="Handy General Services",
            categories=["general", "pest"],
            available_days=list(_ALL_DAYS),
            performance_score=0.80,
            cost_tier=CostTier.LOW,
            pre_approved=True,
            contact_email="hello@handy.example",
        ),
    ]
