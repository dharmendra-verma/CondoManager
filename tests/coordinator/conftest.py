"""Shared fixtures for ``tests/coordinator/``.

Resets the cached maintenance + vendor repository / notifier singletons around
every test so the in-memory stores never leak ticket/vendor state across the
"tool == direct call" and offline assertions.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from agents.maintenance import notifier as maint_notifier_mod
from agents.maintenance import repository as maint_repo_mod
from agents.vendor import notifier as vendor_notifier_mod
from agents.vendor import repository as vendor_repo_mod


@pytest.fixture(autouse=True)
def reset_seams() -> Generator[None, None, None]:
    maint_repo_mod._reset_for_tests()
    maint_notifier_mod._reset_for_tests()
    vendor_repo_mod._reset_for_tests()
    vendor_notifier_mod._reset_for_tests()
    yield
    maint_repo_mod._reset_for_tests()
    maint_notifier_mod._reset_for_tests()
    vendor_repo_mod._reset_for_tests()
    vendor_notifier_mod._reset_for_tests()
