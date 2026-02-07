"""ClaimSubmitter protocol - submits collect_pin() transactions via contract bindings."""

from __future__ import annotations

from typing import Protocol

from hvym_pinner.models.records import ClaimResult


class ClaimSubmitter(Protocol):
    """Submits collect_pin() transactions to the Soroban contract."""

    async def submit_claim(self, slot_id: int) -> ClaimResult:
        """Build, sign, and submit a collect_pin() transaction."""
        ...
