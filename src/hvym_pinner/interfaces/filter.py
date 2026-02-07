"""OfferFilter protocol - evaluates PIN offers against local policy."""

from __future__ import annotations

from typing import Protocol

from hvym_pinner.models.events import PinEvent
from hvym_pinner.models.records import FilterResult


class OfferFilter(Protocol):
    """Filters PIN events based on local policy and wallet health."""

    async def evaluate(self, event: PinEvent) -> FilterResult:
        """Evaluate an offer. Returns accept/reject with reason."""
        ...

    async def verify_slot_active(self, slot_id: int) -> bool:
        """Query on-chain to confirm slot is still claimable."""
        ...
