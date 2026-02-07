"""DataAPI protocol - aggregated daemon state for frontend clients."""

from __future__ import annotations

from typing import Protocol

from hvym_pinner.models.records import ActionResult
from hvym_pinner.models.snapshots import (
    ContractSnapshot,
    DashboardSnapshot,
    EarningsSnapshot,
    OfferSnapshot,
    PinSnapshot,
    WalletSnapshot,
)
from hvym_pinner.models.hunter import (
    CycleReport,
    FlagRecord,
    FlagResult,
    HunterSummary,
    TrackedPinSnapshot,
    VerificationLogEntry,
    VerificationResult,
)


class DataAPI(Protocol):
    """Aggregated daemon state for frontend clients.

    This is the sole interface between the daemon backend and any UI.
    All methods return JSON-serializable data.
    """

    # ── Snapshots (read) ──────────────────────────────────

    async def get_dashboard(self) -> DashboardSnapshot:
        """Full daemon state in a single call. The primary frontend entry point."""
        ...

    async def get_offers(self, status: str | None = None) -> list[OfferSnapshot]:
        """List offers, optionally filtered by status."""
        ...

    async def get_approval_queue(self) -> list[OfferSnapshot]:
        """Offers awaiting operator approval (semi-autonomous mode)."""
        ...

    async def get_earnings(self, period: str = "all") -> EarningsSnapshot:
        """Earnings breakdown. period: 'all', '24h', '7d', '30d'."""
        ...

    async def get_pins(self) -> list[PinSnapshot]:
        """All CIDs currently pinned on our node."""
        ...

    async def get_wallet(self) -> WalletSnapshot:
        """Wallet balance and transaction history."""
        ...

    async def get_contract_state(self) -> ContractSnapshot:
        """Current on-chain state: slots, config, our pinner record."""
        ...

    # ── Actions (write) ────────────────────────────────────

    async def approve_offers(self, slot_ids: list[int]) -> list[ActionResult]:
        """Approve queued offers for pinning (semi-autonomous mode)."""
        ...

    async def reject_offers(self, slot_ids: list[int]) -> list[ActionResult]:
        """Reject queued offers (semi-autonomous mode)."""
        ...

    async def set_mode(self, mode: str) -> ActionResult:
        """Switch operating mode ('auto' or 'approve'). Takes effect immediately."""
        ...

    async def update_policy(
        self, min_price: int | None = None, max_content_size: int | None = None
    ) -> ActionResult:
        """Update filter policy at runtime without restart."""
        ...

    # ── CID Hunter (read) ──────────────────────────────────

    async def get_hunter_summary(self) -> HunterSummary:
        """CID Hunter overview for the dashboard."""
        ...

    async def get_tracked_pins(
        self, status: str | None = None
    ) -> list[TrackedPinSnapshot]:
        """All tracked (CID, pinner) pairs. Optionally filter by status."""
        ...

    async def get_suspects(self) -> list[TrackedPinSnapshot]:
        """Pinners that have failed verification."""
        ...

    async def get_flag_history(self) -> list[FlagRecord]:
        """History of flags we've submitted."""
        ...

    async def get_verification_log(
        self,
        cid: str | None = None,
        pinner: str | None = None,
        limit: int = 50,
    ) -> list[VerificationLogEntry]:
        """Detailed verification check history."""
        ...

    async def get_cycle_history(self, limit: int = 10) -> list[CycleReport]:
        """Past verification cycle summaries."""
        ...

    # ── CID Hunter (actions) ───────────────────────────────

    async def verify_now(
        self, cid: str | None = None, pinner: str | None = None
    ) -> list[VerificationResult]:
        """Trigger immediate verification from frontend."""
        ...

    async def flag_pinner(self, pinner_address: str) -> FlagResult:
        """Manually flag a pinner from frontend (bypass threshold)."""
        ...

    async def update_hunter_config(
        self, cycle_interval: int | None = None, failure_threshold: int | None = None
    ) -> ActionResult:
        """Update hunter settings at runtime."""
        ...
