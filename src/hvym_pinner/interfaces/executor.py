"""PinExecutor protocol - handles IPFS pinning operations via Kubo RPC."""

from __future__ import annotations

from typing import Protocol

from hvym_pinner.models.records import PinResult


class PinExecutor(Protocol):
    """Handles the actual IPFS pinning operation against a local Kubo node."""

    async def pin(self, cid: str, gateway: str) -> PinResult:
        """Fetch CID from gateway and pin to local Kubo node."""
        ...

    async def verify_pinned(self, cid: str) -> bool:
        """Check if CID is pinned on our local node."""
        ...

    async def unpin(self, cid: str) -> bool:
        """Remove a pin (e.g., after UNPIN event)."""
        ...
